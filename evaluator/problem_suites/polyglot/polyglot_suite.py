"""The Polyglot problem suite."""

import os
import shutil
import pathlib
import requests
import traceback
import utils.logger as logger

from enum import Enum
from uuid import UUID
from typing import List, Tuple
from models.problem import Problem
from evaluator.models import Sandbox
from models.problem import ProblemTestResult
from evaluator.models import EvaluationRunException
from models.evaluation_run import EvaluationRunErrorCode
from utils.git import init_local_repo_with_initial_commit
from evaluator.sandbox.sandbox_manager import SandboxManager
from evaluator.problem_suites.problem_suite import ProblemSuite
from utils.diff import get_file_diff, apply_diff_to_local_repo, validate_diff_for_local_repo



class PolyglotSuiteLanguage(str, Enum):
    PYTHON = "py"
    JAVASCRIPT = "js"

class PolyglotSuite(ProblemSuite):
    def __init__(self, language: PolyglotSuiteLanguage):
        self.problems = {}
        self.language = language

        # /evaluator/datasets/polyglot_*
        dataset_path = str(pathlib.Path(__file__).parent.parent.parent / "datasets" / f"polyglot_{self.language}")

        logger.info(f"Loading problems from {dataset_path}...")
        
        # Find problems
        problem_names = []
        for entry in os.listdir(dataset_path):
            entry_path = os.path.join(dataset_path, entry)
            if os.path.isdir(entry_path):
                problem_names.append(entry)
        
        logger.debug(f"Found {len(problem_names)} problems")
        
        # Process each problem
        for problem_name in sorted(problem_names):
            problem_dir = os.path.join(dataset_path, problem_name)
            
            # Verify directory exists
            if not os.path.exists(problem_dir):
                logger.fatal(f"Problem directory not found: {problem_name}")
                
            # Check for required files
            required_files = ["instructions.md", f"main.{self.language}", f"solution.{self.language}", f"tests.{self.language}"]
            missing_files = []
            
            for required_file in required_files:
                file_path = os.path.join(problem_dir, required_file)
                if not os.path.exists(file_path):
                    missing_files.append(required_file)
                    
            if missing_files:
                logger.fatal(f"Problem {problem_name} missing files: {missing_files}")
            
            # Read problem statement from instructions.md
            instructions_path = os.path.join(problem_dir, "instructions.md")
            with open(instructions_path, "r") as f:
                problem_statement = f.read()


            # Calculate diff between main.* and solution.*
            main_path = os.path.join(problem_dir, f"main.{self.language}")
            solution_path = os.path.join(problem_dir, f"solution.{self.language}")
            solution_diff = get_file_diff(main_path, solution_path)
            


            # Add the problem to the suite
            self._add_problem(Problem(
                name=f"{problem_name}-{self.language}",

                problem_statement=problem_statement,
                solution_diff=solution_diff
            ))
            
            logger.debug(f"Problem {problem_name} verified successfully")
        
        logger.info(f"Successfully loaded {len(self.problems)} problems from {dataset_path}")



    def copy_problem_files_to_directory(
        self,
        problem: Problem,
        dir: str,
        *,
        include_tests: bool = False
    ) -> None:
        # /evaluator/datasets/polyglot_*/*
        problem_dir = str(pathlib.Path(__file__).parent.parent.parent / "datasets" / f"polyglot_{self.language}" / problem.name.rsplit("-", 1)[0])
        
        # Copy main.*
        shutil.copy2(os.path.join(problem_dir, f"main.{self.language}"), os.path.join(dir, f"main.{self.language}"))
        logger.debug(f"Copied main.{self.language} to {dir} for {problem.name}")

        if include_tests:
            # Copy tests.*
            shutil.copy2(os.path.join(problem_dir, f"tests.{self.language}"), os.path.join(dir, f"tests.{self.language}"))
            logger.debug(f"Copied tests.{self.language} to {dir} for {problem.name}")

        # Initialize git repository with initial commit
        init_local_repo_with_initial_commit(dir, "Initial commit")



    def initialize_eval_sandbox(
        self,
        sandbox_manager: SandboxManager,
        problem: Problem,
        evaluation_run_id: UUID,
        patch: str
    ) -> Sandbox:
        try:
            def _on_mount(temp_dir: str):
                # Create /sandbox/repo directory
                sandbox_repo_dir = os.path.join(temp_dir, "repo")
                os.mkdir(sandbox_repo_dir)

                # Copy problem files to /sandbox/repo
                self.copy_problem_files_to_directory(problem, sandbox_repo_dir, include_tests=True)

                # Validate the patch
                is_valid, error_message = validate_diff_for_local_repo(patch, sandbox_repo_dir)
                if not is_valid:
                    raise EvaluationRunException(
                        EvaluationRunErrorCode.AGENT_INVALID_PATCH,
                        f"{EvaluationRunErrorCode.AGENT_INVALID_PATCH.get_error_message()}: {error_message}"
                    )

                # Apply the patch
                apply_diff_to_local_repo(patch, sandbox_repo_dir)



            return sandbox_manager.initialize_sandbox(
                name=f"eval-sandbox-{problem.name}-{evaluation_run_id}",
                script_path=os.path.join(os.path.dirname(__file__), f"TEST_RUNNER.{self.language}"),
                on_mount=_on_mount
            )

        except EvaluationRunException:
            raise

        except Exception as e:
            raise EvaluationRunException(
                EvaluationRunErrorCode.VALIDATOR_FAILED_INIT_EVAL,
                f"{EvaluationRunErrorCode.VALIDATOR_FAILED_INIT_EVAL.get_error_message()}: {e}\n\nTraceback:\n{traceback.format_exc()}"
            )



    def run_eval_sandbox(
        self,
        sandbox_manager: SandboxManager,
        eval_sandbox: Sandbox,
        timeout_seconds: int
    ) -> Tuple[List[ProblemTestResult], str]:
        try:
            try:
                sandbox_result_with_logs = sandbox_manager.run_sandbox(eval_sandbox, timeout_seconds=timeout_seconds)
                timed_out = False
            # NOTE ADAM: Docker bug
            # except TimeoutError:
            except requests.exceptions.ConnectionError:
                timed_out = True

            if timed_out:
                raise EvaluationRunException(
                    EvaluationRunErrorCode.AGENT_TIMEOUT_RUNNING_EVAL,
                    f"{EvaluationRunErrorCode.AGENT_TIMEOUT_RUNNING_EVAL.get_error_message()}: The agent exceeded the timeout of {timeout_seconds} seconds."
                )

            if not sandbox_result_with_logs.success:
                raise EvaluationRunException(
                    EvaluationRunErrorCode.AGENT_EXCEPTION_RUNNING_EVAL,
                    f"{EvaluationRunErrorCode.AGENT_EXCEPTION_RUNNING_EVAL.get_error_message()}: {sandbox_result_with_logs.error}\n\nTraceback:\n{sandbox_result_with_logs.traceback}"
                )
            
            return [ProblemTestResult(**test) for test in sandbox_result_with_logs.output], sandbox_result_with_logs.logs

        except EvaluationRunException:
            raise

        except Exception as e:
            raise EvaluationRunException(
                EvaluationRunErrorCode.VALIDATOR_FAILED_RUNNING_EVAL,
                f"{EvaluationRunErrorCode.VALIDATOR_FAILED_RUNNING_EVAL.get_error_message()}: {e}\n\nTraceback:\n{traceback.format_exc()}"
            )