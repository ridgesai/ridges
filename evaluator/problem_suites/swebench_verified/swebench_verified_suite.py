"""The SWE-Bench Verified problem suite."""

import os
import json
import time
import threading
import traceback
import utils.logger as logger

from typing import List
from pathlib import Path
from utils.docker import docker_client
from swebench.harness.constants import SWEbenchInstance
from evaluator.problem_suites.problem_suite import ProblemSuite
from models.problem import Problem, ProblemTest, ProblemTestCategory
from swebench.harness.run_evaluation import make_test_spec, run_instance
from swebench.harness.docker_build import build_env_images, build_instance_images
from utils.git import clone_local_repo_at_commit, clone_repo, verify_commit_exists_in_local_repo


class SWEBenchVerifiedSuite(ProblemSuite):
    def __init__(self, dataset_path: str):
        super().__init__(dataset_path)



    def _load_problems(self, dataset_path: str):
        logger.info(f"Loading problems from {dataset_path}...")

        # Make sure the dataset path exists
        if not os.path.exists(dataset_path):
            logger.fatal(f"Dataset not found: {dataset_path}")
        
        # Make sure the swebench_verified.json file exists
        json_path = os.path.join(dataset_path, "swebench_verified.json")
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
        repos_dir = os.path.join(dataset_path, "repos")
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

            # Skip non-arm64 problems
            if make_test_spec(SWEbenchInstance(problem)).arch != "arm64":
                num_skipped_problems += 1
                logger.warning(f"Problem {problem_name} is not an arm64 problem, skipping (skipped {num_skipped_problems} problem(s) so far)")
                continue

            # Convert tests to our format
            tests = []
            for test_name in json.loads(problem.get("PASS_TO_PASS")):
                tests.append(ProblemTest(name=test_name, category=ProblemTestCategory.pass_to_pass))
            for test_name in json.loads(problem.get("FAIL_TO_PASS")):
                tests.append(ProblemTest(name=test_name, category=ProblemTestCategory.fail_to_pass))

            self._add_problem(Problem(
                name=problem_name,

                problem_statement=problem.get("problem_statement"),
                tests=tests,
                solution_diff=problem.get("patch"),
                
                # We will store the entire SWE-Bench problem object in the userdata (this is basically a Dict[str, Any])
                # This is so that we can access metadata like the commit hash later on
                userdata=problem
            ))

            # logger.debug(f"Problem {problem_name} verified successfully")
        
        logger.info(f"Successfully loaded {len(self.problems)} problems from {dataset_path}")



    def copy_problem_files_to_directory(
        self,
        problem: Problem,
        dir: str,
        *,
        include_solution: bool = False
    ):
        # Get the SWE-Bench problem object
        swebench_instance = problem.userdata
        repo = swebench_instance.get("repo")
        commit_hash = swebench_instance.get("base_commit")

        # Convert repository format from "owner/name" to directory name format "owner_name"
        local_repo_dir = os.path.join(self.dataset_path, "repos", repo.replace("/", "_"))
        
        # Clone the appropriate repository at the specific commit that the problem requires
        clone_local_repo_at_commit(local_repo_dir, commit_hash, dir)

        # Copy solution files if requested
        if include_solution:
            # Write solution.diff file
            with open(os.path.join(dir, "solution.diff"), "w") as f:
                f.write(problem["solution_diff"])
            logger.debug(f"Created solution.diff in {dir} for {problem.name}")




    def evaluate_solution_diff(self, sandbox_manager, run_id, problem_name, solution_diff, on_finish, *, timeout=None):
        def _run_evaluation():
            try:
                report = self.run_swebench_evaluation(sandbox_manager, run_id, problem_name, solution_diff, timeout=timeout)

                # Convert to our format
                report = report[problem_name]

                test_results = []
                for category, tests in report["tests_status"].items():
                    for test_name in tests["success"]:
                        test_results.append({
                            "name": test_name,
                            "category": category.lower(),
                            "status": "pass"
                        })
                    for test_name in tests["failure"]:
                        test_results.append({
                            "name": test_name,
                            "category": category.lower(),
                            "status": "fail"
                        })
                
                on_finish({
                    "status": "success",
                    "test_results": test_results,
                    "logs": None # TODO: /logs/run_evaluation/run_id/run_id/{run_instance.log,test_output.txt}
                })
            except Exception as e:
                warn(f"[SWEBENCH] Failed to run evaluation for {problem_name}: {e}")
                on_finish({
                    "status": "error",
                    "error": str(e),
                    "traceback": traceback.format_exc(),
                    "logs": None # TODO: /logs/run_evaluation/run_id/run_id/{run_instance.log,test_output.txt}
                })
        
        thread = threading.Thread(target=_run_evaluation, daemon=True)
        thread.start()



 
    

    def run_swebench_evaluation(self, sandbox_manager, run_id, problem_name, diff, *, timeout=None):
        """
        Runs a SWE-Bench evaluation for the given instance ID on the given patch.
        This is a blocking function.
        Returns the SWE-Bench report.
        """

        problem = self.get_problem(problem_name)
        if not problem:
            warn(f"[SWEBENCH] Problem {problem_name} not found")
            raise ValueError(f"Problem {problem_name} not found")



        # This would be the object that you'd find in swebench_verified.json
        swebench_instance = problem.get("swebench_instance")

        # Need to create a test spec before calling run_instance()
        test_spec = make_test_spec(SWEbenchInstance(**swebench_instance))

        # # Build environment images
        # debug(f"[SWEBENCH] Building environment images for {problem_name}")
        # start_time = time.time()
        # build_successful, build_failed = build_env_images(
        #     client=sandbox_manager.docker,
        #     dataset=[test_spec],
        #     force_rebuild=False,
        #     max_workers=4
        # )
        # elapsed_time = time.time() - start_time
        # if (len(build_failed) > 0):
        #     warn(f"[SWEBENCH] Failed to build environment images for {problem_name}")
        #     raise RuntimeError(f"Failed to build environment images for {problem_name}")
        # debug(f"[SWEBENCH] Successfully built environment images for {problem_name} in {elapsed_time:.1f} seconds")

        # # Build instance images
        # debug(f"[SWEBENCH] Building instance images for {problem_name}")
        # start_time = time.time()
        # build_successful, build_failed = build_instance_images(
        #     client=sandbox_manager.docker,
        #     dataset=[test_spec],
        #     force_rebuild=False,
        #     max_workers=4
        # )
        # elapsed_time = time.time() - start_time
        # if (len(build_failed) > 0):
        #     warn(f"[SWEBENCH] Failed to build instance images for {problem_name}")
        #     raise RuntimeError(f"Failed to build instance images for {problem_name}")
        # debug(f"[SWEBENCH] Successfully built instance images for {problem_name} in {elapsed_time:.1f} seconds")

        # A "prediction" in the context of SWE-Bench is literally just a patch.
        # The model_name_or_path, model_patch, and instance_id keys are required.
        pred = {
            "model_name_or_path": run_id,
            "model_patch": diff,
            "instance_id": problem_name
        }



        # Run the instance using SWE-Bench
        # This actually builds a Docker image for the specific problem.
        # That Docker image has a name similar to "sweb.eval.x86_64.astropy__astropy-12907" (4 GB).
        debug(f"[SWEBENCH] Running evaluation for {problem_name}")
        start_time = time.time()
        result = run_instance(
            test_spec=test_spec,
            pred=pred,
            rm_image=False,
            force_rebuild=False,
            client=sandbox_manager.docker,
            run_id=run_id,
            timeout=timeout,
            rewrite_reports=False
        )

        IS_SWEBENCH_VERSION_LESS_THAN_4_1_0 = True
        if not IS_SWEBENCH_VERSION_LESS_THAN_4_1_0:
            if not result["completed"]:
                warn(f"[SWEBENCH] Evaluation for {problem_name} was not completed")
                raise RuntimeError(f"Evaluation for {problem_name} was not completed")
        
        elapsed_time = time.time() - start_time
        debug(f"[SWEBENCH] Successfully ran evaluation for {problem_name} in {elapsed_time:.1f} seconds")

        # Read the report
        report_path = Path("logs/run_evaluation") / run_id / run_id / problem_name / "report.json"
        with open(report_path) as f:
            report = json.load(f)

        return report
    


    def prebuild_problem_images(self, problem_names: List[str]):
        MAX_WORKERS = 4
        
        test_specs = []

        for problem_name in problem_names:
            if not self.has_problem_name(problem_name):
                continue

            swebench_instance = self.get_problem(problem_name).userdata
            test_specs.append(make_test_spec(SWEbenchInstance(swebench_instance)))

        logger.debug(f"Prebuilding environment images for {len(test_specs)} problems")
        start_time = time.time()
        build_successful, build_failed = build_env_images(
            client=docker_client,
            dataset=test_specs, 
            force_rebuild=False,
            max_workers=MAX_WORKERS
        )
        elapsed_time = time.time() - start_time
        if len(build_failed) > 0:
            logger.warn(f"Failed to prebuild environment images for {len(build_failed)} of {len(test_specs)} problems")
            raise RuntimeError(f"Failed to prebuild environment images for {len(build_failed)} of {len(test_specs)} problems")
        logger.debug(f"Successfully prebuilt environment images for {len(test_specs)} problems in {elapsed_time:.1f} seconds")

        logger.debug(f"Prebuilding instance images for {len(test_specs)} problems")
        start_time = time.time()
        build_successful, build_failed = build_instance_images(
            client=docker_client,
            dataset=test_specs, 
            force_rebuild=False,
            max_workers=MAX_WORKERS
        )
        elapsed_time = time.time() - start_time
        if len(build_failed) > 0:
            logger.warn(f"Failed to prebuild instance images for {len(build_failed)} of {len(test_specs)} problems")
            raise RuntimeError(f"Failed to prebuild instance images for {len(build_failed)} of {len(test_specs)} problems")
        logger.debug(f"Successfully prebuilt instance images for {len(test_specs)} problems in {elapsed_time:.1f} seconds")