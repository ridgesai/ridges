"""Base class for problem suite managers."""

import os
from abc import ABC, abstractmethod

from validator.utils.diff import apply_diff, validate_diff
from validator.utils.logger import debug, info, warn, error
from validator.sandbox.sandbox_manager import SandboxManager



class ProblemSuite(ABC):
    """
    Abstract base class for a problem suite.
    Classes like PolyglotProblemSuite and SWEBenchProblemSuite inherit from this class.
    """

    def __init__(self, problem_suite_path):
        self.problems = {}
        
        self.problem_suite_path = problem_suite_path
        self.load_problems(problem_suite_path)



    def _add_problem(self, name, *, problem_statement, solution_diff, tests, extra=None):
        """
        Add a problem to the suite.
        
        The solution_diff is a diff that is known to be a correct solution to the problem.
        It is only exposed to the agent if the environment variable RIDGES_INCLUDE_GOLD_PATCH is set.
        """

        problem_data = {
            "name": name,
            "problem_statement": problem_statement,
            "solution_diff": solution_diff,
            "tests": tests
        }
        
        if extra:
            problem_data.update(extra)
            
        self.problems[name] = problem_data

    @abstractmethod
    def load_problems(self, problem_suite_path):
        """
        Load problems from the given problem suite path.
        Each inherited class must implement this method according to how their problem suite is structured.
        The inherited class should call the _add_problem() method to add a problem to the suite.
        """
        pass



    @abstractmethod
    def copy_problem_files_to_directory(self, problem_name, dir, *, include_tests=False, include_solution=False):
        """
        Copy all the files required for an agent to solve a specific problem into a given directory.
        Each inherited class must implement this method according to how their problem suite is structured.
        
        Args:
            problem_name: Name of the problem
            dir: Directory to copy files to
            include_tests: Whether to include test files (default=False)
            include_solution: Whether to include solution files (default=False)
        """
        pass

    @abstractmethod 
    def get_test_runner_path(self):
        """
        Return the path to the test runner script for this problem suite.
        Each problem suite can have its own test execution approach.
        The test runner will receive the problem's "tests" field as input through input.json.
        
        The test runner will write its output to output.json, as typical.
        Ignoring the details of the format (check the documentation for SandboxManager.create_sandbox()):
            {
                "test_results": [
                    # For SWEBenchVerified...
                    {"name": "test_m2m_initial_callable", "category": "pass_to_pass", "status": "pass" or "fail" or "skip"},
                    
                    # For Polyglot...
                    {"name": "test_encode_with_a_not_coprime_to_m", "status": "pass" or "fail" or "skip"},

                    ...
                ]
            }

        Returns:
            Path to the test runner Python script
        """
        pass



    def run_agent_in_sandbox_for_problem(self, sandbox_manager, run_id, problem_name, agent_source_code, on_finish, *, timeout=None, include_solution=False):
        """
        Run an agent in a sandbox for the given problem.
        
        on_finish(result): Callback that receives a result when the sandbox finishes
            Success:
                result: {
                    "status": "success",
                    "diff": "..." | None,
                    "logs": <stdout+stderr> | None
                }
            Error:
                result: {
                    "status": "error",
                    "error": "..." | None,
                    "traceback": "..." | None,
                    "logs": <stdout+stderr> | None
                }
        """

        # Get problem data
        problem = self.get_problem(problem_name)
        if not problem:
            error(f"[PROBLEM_SUITE] Problem {problem_name} not found")
            raise ValueError(f"Problem {problem_name} not found")

        info(f"[PROBLEM_SUITE] Starting sandbox to run agent for problem {problem_name}")

        sandbox_id = None

        def on_mount(temp_dir):
            # Create /sandbox/repo directory
            repo_dir = os.path.join(temp_dir, "repo")
            os.makedirs(repo_dir, exist_ok=True)
            
            # Copy problem files to /sandbox/repo
            self.copy_problem_files_to_directory(problem_name, repo_dir, include_solution=include_solution)
            
            # Write agent source code to /sandbox/agent.py
            agent_path = os.path.join(temp_dir, "agent.py")
            with open(agent_path, "w") as f:
                f.write(agent_source_code)

        def _on_finish(result):
            if result.get("status") == "success":
                # Validate the diff before passing it on to the user
                diff = result.get("output")
                result["output"] = None
                result["diff"] = diff

                debug(f"[PROBLEM_SUITE] Validating diff generated by <{sandbox_id}> for {problem_name}")
                is_valid_diff, error_msg = validate_diff(diff, os.path.join(sandbox_manager.get_sandbox_temp_dir(sandbox_id), "repo"))
                if is_valid_diff:
                    debug(f"[PROBLEM_SUITE] Diff generated by <{sandbox_id}> for {problem_name} is valid")
                else:
                    warn(f"[PROBLEM_SUITE] Diff generated by <{sandbox_id}> for {problem_name} is invalid:\n{error_msg}")
                    result["status"] = "error"
                    result["error"] = f"Diff generated by <{sandbox_id}> for {problem_name} is invalid:\n{error_msg}"
            
            info(f"[PROBLEM_SUITE] Finished sandbox to run agent for problem {problem_name}: {result.get('status')}")

            # Call user's original callback
            on_finish(result)
        
        # Create sandbox that runs the AGENT_RUNNER.py script
        agent_runner_path = os.path.join(os.path.dirname(__file__), "AGENT_RUNNER.py")
        sandbox_id = sandbox_manager.create_sandbox(
            script_path=agent_runner_path,
            input_data={"problem_statement": problem.get("problem_statement")},
            env_vars={"RUN_ID": run_id},
            on_mount=on_mount,
            on_finish=_on_finish,
            timeout=timeout
        )

        debug(f"[PROBLEM_SUITE] Started sandbox to run agent for problem {problem_name}")



    def evaluate_solution_diff(self, sandbox_manager, run_id, problem_name, solution_diff, on_finish, *, timeout=None):
        """
        Evaluate a solution diff for the given problem.

        on_finish(result): Callback that receives a result when the sandbox finishes
            Success:
                result: {
                    "status": "success",
                    "test_results": "See the documentation for get_test_runner_path()",
                    "logs": <stdout+stderr> | None
                }
            Error:
                result: {
                    "status": "error",
                    "error": "..." | None,
                    "traceback": "..." | None,
                    "logs": <stdout+stderr> | None
                }
        """

        # Get problem data
        problem = self.get_problem(problem_name)
        if not problem:
            error(f"[PROBLEM_SUITE] Problem {problem_name} not found")
            raise ValueError(f"Problem {problem_name} not found")

        info(f"[PROBLEM_SUITE] Starting sandbox to evaluate solution diff for problem {problem_name}")

        sandbox_id = None

        def on_mount(temp_dir):
            # Create /sandbox/repo directory
            repo_dir = os.path.join(temp_dir, "repo")
            os.makedirs(repo_dir, exist_ok=True)
            
            # Copy problem files to /sandbox/repo
            self.copy_problem_files_to_directory(problem_name, repo_dir, include_tests=True)
            
            # Apply the diff to /sandbox/repo
            debug(f"[PROBLEM_SUITE] Applying agent's solution diff to {repo_dir} for problem {problem_name}")
            success, error_msg = apply_diff(solution_diff, repo_dir)
            if not success:
                raise Exception(f"Failed to apply agent's solution diff: {error_msg}")
            debug(f"[PROBLEM_SUITE] Applied agent's solution diff to {repo_dir} for problem {problem_name}")
        
        def _on_finish(result):
            if result.get("status") == "success":
                test_results = result.get("output")
                result["output"] = None
                result["test_results"] = test_results
            
            info(f"[PROBLEM_SUITE] Finished sandbox to evaluate solution diff for problem {problem_name}: {result.get('status')}")

            on_finish(result)
        
        # Create sandbox with test runner
        sandbox_id = sandbox_manager.create_sandbox(
            script_path=self.get_test_runner_path(),
            input_data={"tests": problem.get("tests")},
            env_vars={"RUN_ID": run_id}, # TODO
            on_mount=on_mount,
            on_finish=_on_finish,
            timeout=timeout
        )

        debug(f"[PROBLEM_SUITE] Started sandbox to evaluate solution diff for problem {problem_name}")



    def get_problems(self):
        """Return the dict of loaded problems."""
        return self.problems

    def get_num_problems(self):
        """Return the number of loaded problems."""
        return len(self.problems)

    def has_problem(self, problem_name):
        """Check if the problem suite contains a problem with the given name."""
        return problem_name in self.problems

    def get_problem(self, problem_name):
        """Return the problem dict with the given name, or None if not found."""
        """You can access .tests, .problem_statement, and .solution_diff from the returned problem."""
        return self.problems.get(problem_name)

    @abstractmethod
    def get_problem_test_count(self, problem_name):
        """
        Return the total number of tests for a given problem.
        Each problem suite may have different test structures.
        """
        pass

    @staticmethod
    def find_problem_in_suites(problem_name: str) -> tuple[str, "ProblemSuite"] | None:
        """Find which suite contains the given problem name.
        
        Args:
            problem_name: Name of the problem to search for
            
        Returns:
            Tuple of (suite_name, suite_instance) if found, None otherwise
        """
        from validator.problem_suites.polyglot.polyglot_suite import PolyglotSuite
        from validator.problem_suites.swebench_verified.swebench_verified_suite import SWEBenchVerifiedSuite
        
        # Define available suites
        suites_to_check = [
            ("polyglot", PolyglotSuite, "validator/datasets/polyglot"),
            ("swebench_verified", SWEBenchVerifiedSuite, "validator/datasets/swebench_verified")
        ]
        
        for suite_name, suite_class, suite_path in suites_to_check:
            try:
                suite = suite_class(suite_path)
                if suite.has_problem(problem_name):
                    return suite_name, suite
            except Exception as e:
                # If suite fails to load, continue to next suite
                continue
        
        return None