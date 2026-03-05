"""Base class for problem suites.

This module defines the ProblemSuite abstract base class and related utilities
for running agent evaluations. Problem suites define:
- How to set up agent sandboxes with problem-specific files
- How to run agent code and capture results
- How to evaluate agent outputs (patches, test results)

The sandbox architecture provides secure isolation:
- Agent code runs in Docker containers with no direct internet access
- Network access is only possible via the proxy to the inference gateway
- All resources are automatically cleaned up after execution
- Timeouts prevent runaway processes

Integration Points:
- ProblemSuite works with SandboxManager to create and run sandboxes
- Agents are invoked via AGENT_RUNNER.py inside the sandbox
- Results are parsed from output.json written by the agent runner
- Evaluation suites (Polyglot, SWE-bench) extend this base class
"""

import os
import requests
import traceback
import utils.logger as logger

from enum import Enum
from uuid import UUID
from models.problem import Problem
from abc import ABC, abstractmethod
from typing import Any, List, Tuple
from models.problem import ProblemTestResult
from evaluator.models import EvaluationRunException
from models.evaluation_run import EvaluationRunErrorCode
from evaluator.sandbox.sandbox_manager import Sandbox, SandboxManager



class ProblemSuiteName(str, Enum):
    swebench_verified = "swebench_verified"
    polyglot_py = "polyglot_py"
    polyglot_js = "polyglot_js"



class ProblemSuite(ABC):
    def _add_problem(self, problem: Problem) -> None:     
        if problem.name in self.problems:
            logger.fatal(f"Problem {problem.name} already exists")
        
        self.problems[problem.name] = problem



    def has_problem_name(self, problem_name: str) -> bool:
        return problem_name in self.problems

    def get_problem(self, problem_name: str) -> Problem:  
        return self.problems.get(problem_name)



    @abstractmethod
    def copy_problem_files_to_directory(
        self,
        problem: Problem,
        dir: str,
        *,
        include_tests: bool = False
    ) -> None:
        pass



    def initialize_agent_sandbox(
        self,
        sandbox_manager: SandboxManager,
        problem: Problem,
        evaluation_run_id: UUID,
        agent_code: str,
        timeout_seconds: int,
        *,
        include_solutions: bool = False,
        include_tests: bool = False
    ) -> Sandbox:
        """Initialize a sandbox for running an agent on a problem.
        
        This creates an isolated Docker environment with:
        - The agent code mounted at /sandbox/agent.py
        - Problem files mounted at /sandbox/repo/
        - Input data containing the problem statement
        - Environment variables for the agent to access
        
        Args:
            sandbox_manager: The SandboxManager to use for container creation
            problem: The problem definition with statement and files
            evaluation_run_id: Unique identifier for this evaluation
            agent_code: The Python code to run as the agent
            timeout_seconds: Maximum execution time for the agent
            include_solutions: Whether to mount the solution.diff (for testing)
            include_tests: Whether to include test files in /sandbox/repo/
            
        Returns:
            Sandbox instance ready to be run
            
        Raises:
            EvaluationRunException: If sandbox initialization fails
            
        File Layout Inside Sandbox:
            /sandbox/
                ├── agent.py          # The agent code to execute
                ├── input.json        # Contains problem_statement
                ├── solution.diff     # Ground truth (if include_solutions=True)
                └── repo/             # Problem files
                    ├── <problem files>
                    └── <tests>       # (if include_tests=True)
        """
        try:
            def _on_mount(temp_dir: str):
                # Write agent code to /sandbox/agent.py
                # This will be loaded and executed by AGENT_RUNNER.py
                with open(os.path.join(temp_dir, "agent.py"), "w") as f:
                    f.write(agent_code)
                
                # Create repository directory for problem files
                sandbox_repo_dir = os.path.join(temp_dir, "repo")
                os.mkdir(sandbox_repo_dir)

                # Copy problem-specific files into the sandbox
                # This gives the agent access to the codebase it needs to modify
                self.copy_problem_files_to_directory(problem, sandbox_repo_dir, include_tests=include_tests)

                # Optionally include the ground truth solution diff
                # This is typically only used for testing/debugging
                if include_solutions:
                    with open(os.path.join(temp_dir, "solution.diff"), "w") as f:
                        f.write(problem.solution_diff)



            # Initialize the sandbox through the manager
            # AGENT_RUNNER.py is the entry point that will load and execute agent.py
            return sandbox_manager.initialize_sandbox(
                name=f"agent-sandbox-{problem.name}-{evaluation_run_id}",
                script_path=os.path.join(os.path.dirname(__file__), "AGENT_RUNNER.py"),
                input_data={
                    "problem_statement": problem.problem_statement
                },
                env_vars={
                    "EVALUATION_RUN_ID": evaluation_run_id,
                    "AGENT_TIMEOUT": str(timeout_seconds)
                },
                on_mount=_on_mount,
                timeout_seconds=timeout_seconds
            )
        except Exception as e:
            # Wrap any initialization errors in an EvaluationRunException
            # This allows the validator to handle it appropriately
            raise EvaluationRunException(
                EvaluationRunErrorCode.VALIDATOR_FAILED_INIT_AGENT,
                f"{EvaluationRunErrorCode.VALIDATOR_FAILED_INIT_AGENT.get_error_message()}: {e}\n\nTraceback:\n{traceback.format_exc()}"
            )



    def run_agent_sandbox(
        self,
        sandbox_manager: SandboxManager,
        agent_sandbox: Sandbox
    ) -> Tuple[str, str]:
        """Execute the agent sandbox and return its output.
        
        This method runs the initialized sandbox and handles various outcomes:
        - Success: Returns agent output (typically a patch) and logs
        - Timeout: Agent exceeded time limit
        - Exception: Agent code raised an error
        
        Args:
            sandbox_manager: The SandboxManager managing this sandbox
            agent_sandbox: The Sandbox instance to execute
            
        Returns:
            Tuple of (agent_output, logs) where agent_output is typically a patch string
            
        Raises:
            EvaluationRunException: If execution fails, times out, or agent raises an error
            
        Note:
            Uses requests.exceptions.ConnectionError to detect timeouts due to a
            known bug in the Docker SDK where wait() raises ConnectionError instead
            of TimeoutError when the timeout is exceeded.
        """
        try:
            try:
                # Execute the sandbox and wait for completion
                sandbox_result_with_logs = sandbox_manager.run_sandbox(agent_sandbox)
                timed_out = False
            # NOTE ADAM: Docker bug
            # Docker SDK raises ConnectionError instead of TimeoutError on timeout
            # See: https://github.com/docker/docker-py/issues/2268
            # except TimeoutError:
            except requests.exceptions.ConnectionError:
                timed_out = True

            if timed_out:
                raise EvaluationRunException(
                    EvaluationRunErrorCode.AGENT_TIMEOUT_RUNNING_AGENT,
                    f"{EvaluationRunErrorCode.AGENT_TIMEOUT_RUNNING_AGENT.get_error_message()}: The agent exceeded the timeout of {agent_sandbox.timeout_seconds} seconds."
                )

            if not sandbox_result_with_logs.success:
                raise EvaluationRunException(
                    EvaluationRunErrorCode.AGENT_EXCEPTION_RUNNING_AGENT,
                    f"{EvaluationRunErrorCode.AGENT_EXCEPTION_RUNNING_AGENT.get_error_message()}: {sandbox_result_with_logs.error}\n\nTraceback:\n{sandbox_result_with_logs.traceback}"
                )
            
            return sandbox_result_with_logs.output, sandbox_result_with_logs.logs

        except EvaluationRunException:
            raise

        except Exception as e:
            raise EvaluationRunException(
                EvaluationRunErrorCode.VALIDATOR_FAILED_RUNNING_AGENT,
                f"{EvaluationRunErrorCode.VALIDATOR_FAILED_RUNNING_AGENT.get_error_message()}: {e}\n\nTraceback:\n{traceback.format_exc()}"
            )


    
    @abstractmethod
    def initialize_eval_sandbox(
        self,
        sandbox_manager: SandboxManager,
        problem: Problem,
        evaluation_run_id: UUID,
        patch: str
    ) -> Any:
        pass



    @abstractmethod
    def run_eval_sandbox(
        self,
        sandbox_manager: SandboxManager,
        eval_sandbox: Any
    ) -> Tuple[List[ProblemTestResult], str]:
        pass