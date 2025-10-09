"""The SWE-Bench Verified problem suite."""

import os
import json
import time
import threading
import traceback

from pathlib import Path
from validator.utils.diff import apply_diff
from validator.utils.logger import debug, info, warn, error
from validator.problem_suites.problem_suite import ProblemSuite
from swebench.harness.constants import SWEbenchInstance
from swebench.harness.run_evaluation import make_test_spec, run_instance
from swebench.harness.docker_build import build_env_images, build_instance_images
from validator.utils.git import clone_local_repo_at_commit, clone_repo, verify_commit_exists



class SWEBenchVerifiedSuite(ProblemSuite):
    def __init__(self, problem_suite_path):
        super().__init__(problem_suite_path)



    def load_problems(self, problem_suite_path):
        """Load problems from swebench_verified.json and verify directory structure."""

        if not os.path.exists(problem_suite_path):
            error(f"[SWEBENCH] Problem suite directory not found: {problem_suite_path}")
            raise FileNotFoundError(f"Problem suite directory not found: {problem_suite_path}")
            
        json_path = os.path.join(problem_suite_path, "swebench_verified.json")
        if not os.path.exists(json_path):
            error(f"[SWEBENCH] swebench_verified.json not found at: {json_path}")
            raise FileNotFoundError(f"swebench_verified.json not found at: {json_path}")
            
        try:
            with open(json_path, "r") as f:
                problems_list = json.load(f)
            
            info(f"[SWEBENCH] Loaded {len(problems_list)} problems from {json_path}")
            
            # Count unique repositories
            unique_repos = set()
            for problem in problems_list:
                repo = problem.get("repo")
                if repo:
                    unique_repos.add(repo)
            
            debug(f"[SWEBENCH] Finding {len(unique_repos)} unique repositories")
            
            # Check that all repositories exist in the repos/ directory
            repos_dir = os.path.join(problem_suite_path, "repos")
            if not os.path.exists(repos_dir):
                os.makedirs(repos_dir, exist_ok=True)
            
            for repo in unique_repos:
                # Convert repository format from "owner/name" to directory name format "owner_name"
                repo_dir_name = repo.replace("/", "_")
                repo_path = os.path.join(repos_dir, repo_dir_name)
                
                if not os.path.exists(repo_path):
                    repo_url = f"https://github.com/{repo}.git"
                    success, error_msg = clone_repo(repo_url, repo_path)
                    if not success:
                        raise RuntimeError(f"Failed to clone repository {repo}: {error_msg}")
            
            debug(f"[SWEBENCH] Found {len(unique_repos)} unique repositories")
            
            # Process each problem
            for problem in problems_list:
                instance_id = problem.get("instance_id")
                
                repo = problem.get("repo")
                base_commit = problem.get("base_commit")
                
                # # Verify commit exists in repository
                # repo_dir_name = repo.replace("/", "_")
                # repo_path = os.path.join(repos_dir, repo_dir_name)
                
                # if not verify_commit_exists(repo_path, base_commit):
                #     error(f"[SWEBENCH] Problem {instance_id}: commit {base_commit} not found in repository {repo}")
                #     raise ValueError(f"Problem {instance_id}: commit {base_commit} not found in repository {repo}")
                
                # debug(f"[SWEBENCH] Verified commit {base_commit} exists in {repo} for problem {instance_id}")
                
                self._add_problem(
                    instance_id, 
                    problem_statement=problem.get("problem_statement"), 
                    solution_diff=problem.get("patch"), 
                    tests={
                        "pass_to_pass": json.loads(problem.get("PASS_TO_PASS")),
                        "fail_to_pass": json.loads(problem.get("FAIL_TO_PASS"))
                    },
                    extra={
                        "swebench_instance": problem
                    }
                )
            
            info(f"[SWEBENCH] Successfully loaded {len(self.problems)} problems")
            
        except Exception as e:
            error(f"[SWEBENCH] Failed to load problems: {e}")
            raise e



    def copy_problem_files_to_directory(self, problem_name, dir, *, include_tests=False, include_solution=False):
        """Copy problem files to the given directory."""
        
        problem = self.get_problem(problem_name)
        if not problem:
            warn(f"[SWEBENCH] Problem {problem_name} not found")
            raise ValueError(f"Problem {problem_name} not found")

        # Get repository and commit information from metadata
        swebench_instance = problem.get("swebench_instance")
        repo = swebench_instance.get("repo")
        base_commit = swebench_instance.get("base_commit")

        # Convert repository format from "owner/name" to directory name format "owner_name"
        repo_dir_name = repo.replace("/", "_")
        repo_path = os.path.join(self.problem_suite_path, "repos", repo_dir_name)
        
        # Clone the appropriate repository at the specific commit that the problem requires
        debug(f"[SWEBENCH] Cloning {repo} at commit {base_commit} to {dir} for {problem_name}")
        success, error_msg = clone_local_repo_at_commit(repo_path, base_commit, dir)
        if not success:
            warn(f"[SWEBENCH] Failed to clone repository for {problem_name}: {error_msg}")
            raise RuntimeError(f"Failed to clone repository for {problem_name}: {error_msg}")
        debug(f"[SWEBENCH] Successfully copied repository files for {problem_name}")

        # REMOVED: Falling back to SWE-Bench Harness
        #
        # # Copy test files if requested
        # if include_tests:
        #     test_patch = swebench_instance.get("test_patch")
        #     debug(f"[SWEBENCH] Applying test patch for {problem_name}")
        #     success, error_msg = apply_diff(test_patch, dir)
        #     if not success:
        #         error(f"[SWEBENCH] Failed to apply test patch for {problem_name}: {error_msg}")
        #         raise RuntimeError(f"Failed to apply test patch for {problem_name}: {error_msg}")
        #     debug(f"[SWEBENCH] Successfully applied test patch for {problem_name}")
        
        # Copy solution files if requested
        if include_solution:
            # Write solution.diff file
            with open(os.path.join(dir, "solution.diff"), "w") as f:
                f.write(problem["solution_diff"])
            debug(f"[SWEBENCH] Created solution.diff in {dir} for {problem_name}")



    def get_test_runner_path(self):
        return os.path.join(os.path.dirname(__file__), "TEST_RUNNER.py")



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



    def get_problem_test_count(self, problem_name):
        problem = self.get_problem(problem_name)
        if not problem:
            return 0
        
        tests = problem.get("tests", {})
        pass_to_pass = tests.get("pass_to_pass")
        fail_to_pass = tests.get("fail_to_pass")
        
        return len(pass_to_pass) + len(fail_to_pass)

    

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
    


    def prebuild_problem_images(self, sandbox_manager, problem_names):
        test_specs = []

        for problem_name in problem_names:
            problem = self.get_problem(problem_name)
            if not problem:
                warn(f"[SWEBENCH] Problem {problem_name} not found")
                continue

            test_specs.append(make_test_spec(SWEbenchInstance(**problem.get("swebench_instance"))))

        debug(f"[SWEBENCH] Prebuilding environment images for {len(test_specs)} problems")
        start_time = time.time()
        build_successful, build_failed = build_env_images(
            client=sandbox_manager.docker,
            dataset=test_specs, 
            force_rebuild=False,
            max_workers=(os.cpu_count() or 4)-1
        )
        elapsed_time = time.time() - start_time
        if len(build_failed) > 0:
            warn(f"[SWEBENCH] Failed to prebuild environment images for {len(build_failed)} of {len(test_specs)} problems")
            raise RuntimeError(f"Failed to prebuild environment images for {len(build_failed)} of {len(test_specs)} problems")
        debug(f"[SWEBENCH] Successfully prebuilt environment images for {len(test_specs)} problems in {elapsed_time:.1f} seconds")

        debug(f"[SWEBENCH] Prebuilding instance images for {len(test_specs)} problems")
        start_time = time.time()
        build_successful, build_failed = build_instance_images(
            client=sandbox_manager.docker,
            dataset=test_specs, 
            force_rebuild=False,
            max_workers=(os.cpu_count() or 4)-1
        )
        elapsed_time = time.time() - start_time
        if len(build_failed) > 0:
            warn(f"[SWEBENCH] Failed to prebuild instance images for {len(build_failed)} of {len(test_specs)} problems")
            raise RuntimeError(f"Failed to prebuild instance images for {len(build_failed)} of {len(test_specs)} problems")
        debug(f"[SWEBENCH] Successfully prebuilt instance images for {len(test_specs)} problems in {elapsed_time:.1f} seconds")