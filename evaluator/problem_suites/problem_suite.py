"""Base class for problem suites."""

import os
import utils.logger as logger
import validator.config as config

from uuid import uuid4
from models.problem import Problem
from abc import ABC, abstractmethod
from utils.temp import create_temp_dir
from evaluator.sandbox.sandbox_manager import SandboxManager



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
        include_solution: bool = False
    ):
        """
        Copies the problem files to the given directory.
        Each inherited class must implement this method according to how their problem suite is structured.
        """
        
        pass



    def initialize_agent_sandbox(self, sandbox_manager: SandboxManager, problem: Problem, agent_code: str, *, include_solution: bool = False):
        def _on_mount(temp_dir: str):
            # Create /sandbox/repo directory
            sandbox_repo_dir = os.path.join(temp_dir, "repo")
            os.mkdir(sandbox_repo_dir)

            # Copy problem files to /sandbox/repo
            self.copy_problem_files_to_directory(problem, sandbox_repo_dir, include_solution=include_solution)

            # Write agent code to /sandbox/agent.py
            with open(os.path.join(temp_dir, "agent.py"), "w") as f:
                f.write(agent_code)

        sandbox = sandbox_manager.initialize_sandbox(
            name=f"polglot-agent-sandbox-{problem.name}",
            on_mount=_on_mount,
            env_vars={
                "RUN_ID": str(uuid4())
            },
            python_script_path=os.path.join(os.path.dirname(__file__), "AGENT_RUNNER.py"),
            input_data={
                "problem_statement": problem.statement
            },
            timeout_seconds=config.AGENT_SANDBOX_TIMEOUT_SECONDS
        )

        return sandbox