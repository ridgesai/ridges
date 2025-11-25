"""The Polyglot problem suite."""

import os
import json
import shutil
import requests
import traceback
import utils.logger as logger

from uuid import UUID
from typing import List, Tuple
from evaluator.models import Sandbox
from models.problem import ProblemTestResult
from evaluator.models import EvaluationRunException
from models.evaluation_run import EvaluationRunErrorCode
from utils.git import init_local_repo_with_initial_commit
from evaluator.sandbox.sandbox_manager import SandboxManager
from evaluator.problem_suites.problem_suite import ProblemSuite
from models.problem import Problem, ProblemTest, ProblemTestCategory
from utils.diff import get_file_diff, apply_diff_to_local_repo, validate_diff_for_local_repo



class PolyglotPythonSuite(ProblemSuite):
    def __init__(self, dataset_path: str):
        super().__init__(dataset_path)



    def _load_problems(self, dataset_path: str):
        logger.info(f"Loading problems from {dataset_path}...")

        # Make sure the dataset path exists
        if not os.path.exists(dataset_path):
            logger.fatal(f"Dataset not found: {dataset_path}")
            
        # Make sure the polyglot_py.json file exists
        json_path = os.path.join(dataset_path, "polyglot_py.json")
        if not os.path.exists(json_path):
            logger.fatal(f"polyglot_py.json not found: {json_path}")
            
        # Open the polyglot_py.json file
        with open(json_path, "r") as f:
            problem_list = json.load(f)
        
        logger.debug(f"Loaded {len(problem_list)} problems from {json_path}")
        
        # Process each problem
        for problem in problem_list:
            # Parse problem name
            problem_name = problem.get("name")
            if not problem_name:
                logger.fatal("Problem missing name field")
            
            problem_dir = os.path.join(dataset_path, problem_name)
            
            # Verify directory exists
            if not os.path.exists(problem_dir):
                logger.fatal(f"Problem directory not found: {problem_name}")
                
            # Check for required files
            required_files = ["main.py", "tests.py", "instructions.md", "solution.py"]
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

            # Parse problem tests
            test_names = problem.get("tests")
            if not test_names:
                logger.fatal(f"Problem {problem_name} missing tests field")

            tests = [ProblemTest(name=test_name, category=ProblemTestCategory.default) for test_name in test_names]

            # Calculate diff between main.py and solution.py
            main_path = os.path.join(problem_dir, "main.py")
            solution_path = os.path.join(problem_dir, "solution.py")
            solution_diff = get_file_diff(main_path, solution_path)
            


            # Add the problem to the suite
            self._add_problem(Problem(
                name=problem_name,

                problem_statement=problem_statement,
                tests=tests,
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
        problem_dir = os.path.join(self.dataset_path, problem.name)
        
        # Copy main.py
        shutil.copy2(os.path.join(problem_dir, "main.py"), os.path.join(dir, "main.py"))
        logger.debug(f"Copied main.py to {dir} for {problem.name}")

        if include_tests:
            # Copy tests.py
            shutil.copy2(os.path.join(problem_dir, "tests.py"), os.path.join(dir, "tests.py"))
            logger.debug(f"Copied tests.py to {dir} for {problem.name}")

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
                python_script_path=os.path.join(os.path.dirname(__file__), "TEST_RUNNER.py"),
                input_data=[test.model_dump() for test in problem.tests],
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