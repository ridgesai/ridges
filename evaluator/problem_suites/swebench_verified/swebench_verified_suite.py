"""The SWE-Bench Verified problem suite."""

import os
import json
import time
import pathlib
import traceback
import utils.logger as logger

from uuid import UUID
from pydantic import BaseModel
from utils.docker import get_docker_client
from typing import Any, Dict, List, Tuple, Optional
from utils.diff import validate_diff_for_local_repo, apply_diff_to_local_repo, validate_patched_files_syntax
from evaluator.models import EvaluationRunException
from swebench.harness.constants import SWEbenchInstance
from utils.temp import create_temp_dir, delete_temp_dir
from models.evaluation_run import EvaluationRunErrorCode
from swebench.harness.test_spec.test_spec import TestSpec
from evaluator.sandbox.sandbox_manager import SandboxManager
from swebench.harness.run_evaluation import make_test_spec, run_instance
from swebench.harness.docker_build import build_env_images, build_instance_images
from evaluator.problem_suites.problem_suite import ProblemSuite, ProblemSuiteName
from utils.git import clone_repo, clone_local_repo_at_commit, reset_local_repo_to_commit, verify_commit_exists_in_local_repo
from models.problem import Problem, ProblemTest, ProblemDifficulty, ProblemTestResult, ProblemTestCategory, ProblemTestResultStatus



def _swebench_verified_difficulty_to_problem_difficulty(difficulty: str) -> Optional[ProblemDifficulty]:
    if difficulty == "<15 min fix":
        return ProblemDifficulty.EASY
    elif difficulty == "15 min - 1 hour":
        return ProblemDifficulty.MEDIUM
    elif difficulty == "1-4 hours":
        return ProblemDifficulty.HARD
    elif difficulty == ">4 hours":
        return ProblemDifficulty.IMPOSSIBLE
    else:
        return None



# /evaluator/datasets/swebench_verified
SWEBENCH_VERIFIED_DATASET_PATH = str(pathlib.Path(__file__).parent.parent.parent / "datasets" / "swebench_verified")



class SWEBenchVerifiedEvaluationSandbox(BaseModel):
    evaluation_run_id: UUID
    test_spec: TestSpec
    pred: Dict[str, Any]
    timeout_seconds: int



class SWEBenchVerifiedSuite(ProblemSuite):
    def __init__(self):
        self.problems = {}
        self.name = ProblemSuiteName.swebench_verified
        
        logger.info(f"Loading problems from {SWEBENCH_VERIFIED_DATASET_PATH}...")
        
        # Make sure the swebench_verified.json file exists
        json_path = os.path.join(SWEBENCH_VERIFIED_DATASET_PATH, "swebench_verified.json")
        if not os.path.exists(json_path):
            logger.fatal(f"swebench_verified.json not found at: {json_path}")
            
        # Open the swebench_verified.json file
        with open(json_path, "r") as f:
            problems_list = json.load(f)
        
        logger.debug(f"Loaded {len(problems_list)} problems from {json_path}")
        
        # Count unique repositories
        unique_repos = set()
        for problem in problems_list:
            unique_repos.add(problem.get("repo"))
        
        logger.debug(f"Finding {len(unique_repos)} unique repositories...")
        
        # Check that all repositories exist in the repos/ directory
        repos_dir = os.path.join(SWEBENCH_VERIFIED_DATASET_PATH, "repos")
        if not os.path.exists(repos_dir):
            os.makedirs(repos_dir, exist_ok=True)
        
        for repo in unique_repos:
            # Convert repository format from "owner/name" to directory name format "owner_name"
            repo_dir_name = repo.replace("/", "_")
            repo_path = os.path.join(repos_dir, repo_dir_name)
            
            if not os.path.exists(repo_path):
                repo_url = f"https://github.com/{repo}.git"
                clone_repo(repo_url, repo_path)
        
        logger.debug(f"Found {len(unique_repos)} unique repositories")
        
        # Process each problem
        num_skipped_problems = 0
        for problem in problems_list:
            problem_name = problem.get("instance_id")
            
            # repo = problem.get("repo")
            # base_commit = problem.get("base_commit")
            
            # # Verify commit exists in repository
            # repo_dir_name = repo.replace("/", "_")
            # repo_path = os.path.join(repos_dir, repo_dir_name)
        
            # if not verify_commit_exists_in_local_repo(repo_path, base_commit):
            #     logger.fatal(f"Problem {problem_name}: commit {base_commit} not found in repository {repo}")

            # # Skip non-arm64 problems
            # architecture = make_test_spec(SWEbenchInstance(problem)).arch
            # if architecture != "arm64":
            #     num_skipped_problems += 1
            #     logger.warning(f"Problem {problem_name} is not an arm64 problem (is {architecture}), skipping (skipped {num_skipped_problems} problem(s) so far)")
            #     continue

            # Convert tests to our format
            tests = []
            for test_name in json.loads(problem.get("PASS_TO_PASS")):
                tests.append(ProblemTest(name=test_name, category=ProblemTestCategory.pass_to_pass))
            for test_name in json.loads(problem.get("FAIL_TO_PASS")):
                tests.append(ProblemTest(name=test_name, category=ProblemTestCategory.fail_to_pass))

            self._add_problem(Problem(
                name=problem_name,
                difficulty=_swebench_verified_difficulty_to_problem_difficulty(problem.get("difficulty")),

                problem_statement=problem.get("problem_statement"),
                solution_diff=problem.get("patch"),
                
                # We will store the entire SWE-Bench problem object in the userdata (this is basically a Dict[str, Any])
                # This is so that we can access metadata like the commit hash later on
                userdata=problem
            ))

            # logger.debug(f"Problem {problem_name} verified successfully")
        
        logger.info(f"Successfully loaded {len(self.problems)} problems from {SWEBENCH_VERIFIED_DATASET_PATH}")



    def copy_problem_files_to_directory(
        self,
        problem: Problem,
        dir: str,
        *,
        include_tests: bool = False # TODO ADAM
    ) -> None:
        # Get the SWE-Bench problem object
        swebench_instance = problem.userdata
        repo = swebench_instance.get("repo")
        commit_hash = swebench_instance.get("base_commit")

        # Convert repository format from "owner/name" to directory name format "owner_name"
        local_repo_dir = os.path.join(SWEBENCH_VERIFIED_DATASET_PATH, "repos", repo.replace("/", "_"))
        
        # Clone the appropriate repository at the specific commit that the problem requires
        clone_local_repo_at_commit(local_repo_dir, commit_hash, dir)

        # Remove future commits from repo
        reset_local_repo_to_commit(local_repo_dir, commit_hash, dir)


    def initialize_eval_sandbox(
        self,
        sandbox_manager: SandboxManager,
        problem: Problem,
        evaluation_run_id: UUID,
        patch: str,
        timeout_seconds: int
    ) -> SWEBenchVerifiedEvaluationSandbox:
        try:
            # Create temporary directory
            temp_dir = create_temp_dir()

            # Copy problem files to temporary directory
            self.copy_problem_files_to_directory(problem, temp_dir, include_tests=True)

            # Validate the patch
            is_valid, error_message = validate_diff_for_local_repo(patch, temp_dir)
            if not is_valid:
                raise EvaluationRunException(
                    EvaluationRunErrorCode.AGENT_INVALID_PATCH,
                    f"{EvaluationRunErrorCode.AGENT_INVALID_PATCH.get_error_message()}: {error_message}"
                )

            # Syntax-check the patched files
            apply_diff_to_local_repo(patch, temp_dir)
            is_valid, error_message = validate_patched_files_syntax(temp_dir)
            if not is_valid:
                raise EvaluationRunException(
                    EvaluationRunErrorCode.AGENT_INVALID_PATCH,
                    f"{EvaluationRunErrorCode.AGENT_INVALID_PATCH.get_error_message()}: {error_message}"
                )

            swebench_instance = problem.userdata

            test_spec = make_test_spec(SWEbenchInstance(**swebench_instance))

            pred = {
                "model_name_or_path": str(evaluation_run_id),
                "model_patch": patch,
                "instance_id": problem.name
            }

            return SWEBenchVerifiedEvaluationSandbox(evaluation_run_id=evaluation_run_id, test_spec=test_spec, pred=pred, timeout_seconds=timeout_seconds)
        
        except EvaluationRunException:
            raise
        
        except Exception as e:
            raise EvaluationRunException(
                EvaluationRunErrorCode.VALIDATOR_FAILED_INIT_EVAL,
                f"{EvaluationRunErrorCode.VALIDATOR_FAILED_INIT_EVAL.get_error_message()}: {e}\n\nTraceback:\n{traceback.format_exc()}"
            )
        
        finally:
            # Delete temporary directory
            delete_temp_dir(temp_dir)

    

    def run_eval_sandbox(
        self,
        sandbox_manager: SandboxManager,
        eval_sandbox: SWEBenchVerifiedEvaluationSandbox,
    ) -> Tuple[List[ProblemTestResult], str]:
        try:
            instance_id, report = run_instance(
                test_spec=eval_sandbox.test_spec,
                pred=eval_sandbox.pred,
                rm_image=False,
                force_rebuild=False,
                client=get_docker_client(),
                run_id=str(eval_sandbox.evaluation_run_id),
                timeout=eval_sandbox.timeout_seconds
            )

            # NOTE ADAM: timeout

            test_results = []
            
            tests_status = report[instance_id]["tests_status"]
            
            for test_name in tests_status["FAIL_TO_PASS"]["success"]:
                test_results.append(ProblemTestResult(name=test_name, category=ProblemTestCategory.fail_to_pass, status=ProblemTestResultStatus.PASS))
            for test_name in tests_status["FAIL_TO_PASS"]["failure"]:
                test_results.append(ProblemTestResult(name=test_name, category=ProblemTestCategory.fail_to_pass, status=ProblemTestResultStatus.FAIL))
            
            for test_name in tests_status["PASS_TO_PASS"]["success"]:
                test_results.append(ProblemTestResult(name=test_name, category=ProblemTestCategory.pass_to_pass, status=ProblemTestResultStatus.PASS))
            for test_name in tests_status["PASS_TO_PASS"]["failure"]:
                test_results.append(ProblemTestResult(name=test_name, category=ProblemTestCategory.pass_to_pass, status=ProblemTestResultStatus.FAIL))
            
            # NOTE ADAM: /logs/run_evaluation/run_id/run_id/{run_instance.log,test_output.txt}
            eval_logs = "No evaluation logs available"

            return test_results, eval_logs

        except Exception as e:
            raise EvaluationRunException(
                EvaluationRunErrorCode.VALIDATOR_FAILED_RUNNING_EVAL,
                f"{EvaluationRunErrorCode.VALIDATOR_FAILED_RUNNING_EVAL.get_error_message()}: {e}\n\nTraceback:\n{traceback.format_exc()}"
            )
    


    def prebuild_problem_images(self, problem_names: List[str]):
        MAX_WORKERS = 4

        problem_names = sorted({name for name in problem_names if self.has_problem_name(name)})

        if len(problem_names) == 0:
            return

        logger.info(f"Prebuilding problem images:")
        for problem_name in problem_names:
            logger.info(f"  {problem_name}")
        
        test_specs = []

        for problem_name in problem_names:
            if not self.has_problem_name(problem_name):
                continue

            swebench_instance = self.get_problem(problem_name).userdata
            test_specs.append(make_test_spec(SWEbenchInstance(swebench_instance)))

        logger.debug(f"Prebuilding environment images for {len(test_specs)} problems")
        start_time = time.time()
        build_successful, build_failed = build_env_images(
            client=get_docker_client(),
            dataset=test_specs, 
            force_rebuild=False,
            max_workers=MAX_WORKERS
        )
        elapsed_time = time.time() - start_time
        if len(build_failed) > 0:
            logger.warning(f"Failed to prebuild environment images for {len(build_failed)} of {len(test_specs)} problems")
            raise RuntimeError(f"Failed to prebuild environment images for {len(build_failed)} of {len(test_specs)} problems")
        logger.debug(f"Successfully prebuilt environment images for {len(test_specs)} problems in {elapsed_time:.1f} seconds")

        logger.debug(f"Prebuilding instance images for {len(test_specs)} problems")
        start_time = time.time()
        build_successful, build_failed = build_instance_images(
            client=get_docker_client(),
            dataset=test_specs, 
            force_rebuild=False,
            max_workers=MAX_WORKERS
        )
        elapsed_time = time.time() - start_time
        if len(build_failed) > 0:
            logger.warning(f"Failed to prebuild instance images for {len(build_failed)} of {len(test_specs)} problems")
            raise RuntimeError(f"Failed to prebuild instance images for {len(build_failed)} of {len(test_specs)} problems")
        logger.debug(f"Successfully prebuilt instance images for {len(test_specs)} problems in {elapsed_time:.1f} seconds")



SWEBENCH_VERIFIED_SUITE = SWEBenchVerifiedSuite()