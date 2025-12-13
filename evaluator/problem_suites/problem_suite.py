"""Base class for problem suites."""

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
        try:
            def _on_mount(temp_dir: str):
                # Create /sandbox/agent.py
                with open(os.path.join(temp_dir, "agent.py"), "w") as f:
                    f.write(agent_code)
                
                # Create /sandbox/repo directory
                sandbox_repo_dir = os.path.join(temp_dir, "repo")
                os.mkdir(sandbox_repo_dir)

                # Copy problem files to /sandbox/repo
                self.copy_problem_files_to_directory(problem, sandbox_repo_dir, include_tests=include_tests)

                if include_solutions:
                    # Create /sandbox/solution.diff
                    with open(os.path.join(temp_dir, "solution.diff"), "w") as f:
                        f.write(problem.solution_diff)



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
            raise EvaluationRunException(
                EvaluationRunErrorCode.VALIDATOR_FAILED_INIT_AGENT,
                f"{EvaluationRunErrorCode.VALIDATOR_FAILED_INIT_AGENT.get_error_message()}: {e}\n\nTraceback:\n{traceback.format_exc()}"
            )



    def run_agent_sandbox(
        self,
        sandbox_manager: SandboxManager,
        agent_sandbox: Sandbox
    ) -> Tuple[str, str]:
        try:
            try:
                sandbox_result_with_logs = sandbox_manager.run_sandbox(agent_sandbox)
                timed_out = False
            # NOTE ADAM: Docker bug
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