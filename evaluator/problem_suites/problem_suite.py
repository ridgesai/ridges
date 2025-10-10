"""Base class for problem suites."""

import os
import traceback
import utils.logger as logger
import validator.config as config

from uuid import UUID
from typing import Tuple
from models.problem import Problem
from abc import ABC, abstractmethod
from utils.temp import create_temp_dir
from evaluator.models import EvaluationRunException
from models.evaluation_run import EvaluationRunErrorCode
from evaluator.sandbox.sandbox_manager import Sandbox, SandboxManager



class ProblemSuite(ABC):
    """
    Abstract base class for a problem suite.
    Classes like PolyglotProblemSuite and SWEBenchProblemSuite inherit from this class.
    """



    def __init__(self, dataset_path: str):
        self.problems = {}
        
        self.dataset_path = dataset_path
        self._load_problems(dataset_path)



    def _add_problem(self, problem: Problem) -> None:
        """
        Adds a problem to the problem suite.
        Should only be called from within the load_problems() implementation of a derived class.
        """
        
        if problem.name in self.problems:
            logger.fatal(f"Problem {problem.name} already exists in the suite")
        
        self.problems[problem.name] = problem

    @abstractmethod
    def _load_problems(self, dataset_path: str):
        """
        Loads all the problems from the given dataset path.
        Each inherited class must implement this method according to how their problem suite is structured.
        The implementation should call the _add_problem() method as many times as needed.
        """

        pass

    def has_problem_name(self, problem_name: str) -> bool:
        """
        Returns True if the problem suite has a problem with the given name.
        """
        
        return problem_name in self.problems

    def get_problem(self, problem_name: str) -> Problem:
        """
        Returns the problem with the given name.
        """
        
        return self.problems.get(problem_name)



    @abstractmethod
    def copy_problem_files_to_directory(
        self,
        problem: Problem,
        dir: str,
        *,
        include_tests: bool = False
    ):
        """
        Copies the problem files to the given directory.
        Each inherited class must implement this method according to how their problem suite is structured.
        """
        
        pass



    def initialize_agent_sandbox(
        self,
        sandbox_manager: SandboxManager,
        problem: Problem,
        evaluation_run_id: UUID,
        agent_code: str,
        *,
        include_solution: bool = False
    ) -> Sandbox:
        # TODO ADAM: Docs
    
        def _on_mount(temp_dir: str):
            # Create /sandbox/agent.py
            with open(os.path.join(temp_dir, "agent.py"), "w") as f:
                f.write(agent_code)
            
            # Create /sandbox/repo directory
            sandbox_repo_dir = os.path.join(temp_dir, "repo")
            os.mkdir(sandbox_repo_dir)

            # Copy problem files to /sandbox/repo
            self.copy_problem_files_to_directory(problem, sandbox_repo_dir, include_solution=include_solution)

            if include_solution:
                # Create /sandbox/solution.diff
                with open(os.path.join(temp_dir, "solution.diff"), "w") as f:
                    f.write(problem.solution_diff)



        return sandbox_manager.initialize_sandbox(
            name=f"agent-sandbox-{problem.name}",
            python_script_path=os.path.join(os.path.dirname(__file__), "AGENT_RUNNER.py"),
            input_data={
                "problem_statement": problem.problem_statement
            },
            env_vars={
                "EVALUATION_RUN_ID": evaluation_run_id
            },
            on_mount=_on_mount,
        )



    def run_agent_sandbox(
        self,
        sandbox_manager: SandboxManager,
        sandbox: Sandbox
    ) -> Tuple[str, str]:
        # TODO ADAM: Docs

        try:
            sandbox_result_with_logs = sandbox_manager.run_sandbox(sandbox, timeout_seconds=config.AGENT_TIMEOUT_SECONDS)

            if not sandbox_result_with_logs.success:
                raise EvaluationRunException(
                    EvaluationRunErrorCode.AGENT_EXCEPTION,
                    f"{EvaluationRunErrorCode.AGENT_EXCEPTION.get_error_message()}: {sandbox_result_with_logs.error}\n\nTraceback:\n{sandbox_result_with_logs.traceback}"
                )
            
            return sandbox_result_with_logs.output, sandbox_result_with_logs.logs

        except Exception as e:
            raise EvaluationRunException(
                EvaluationRunErrorCode.VALIDATOR_FAILED_RUNNING_AGENT,
                f"{EvaluationRunErrorCode.VALIDATOR_FAILED_RUNNING_AGENT.get_error_message()}: {e}\n\nTraceback:\n{traceback.format_exc()}"
            )


    
    @abstractmethod
    def initialize_eval_sandbox(
        self,
        sandbox_manager: SandboxManager,
        problem_name: str
    ) -> Sandbox:
        # TODO ADAM: Docs
        pass



    @abstractmethod
    def run_eval_sandbox(
        self,
        sandbox_manager: SandboxManager,
        problem_name: str
    ) -> Sandbox:
        # TODO ADAM: Docs
        pass