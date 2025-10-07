"""The Polyglot problem suite."""

import os
import json
import shutil

from validator.utils.diff import get_file_diff
from validator.utils.logger import debug, info, warn, error
from validator.utils.git import init_repo_with_initial_commit
from validator.problem_suites.problem_suite import ProblemSuite


class PolyglotSuite(ProblemSuite):
    def __init__(self, problem_suite_path):
        super().__init__(problem_suite_path)



    def load_problems(self, problem_suite_path):
        """Load problems from polyglot.json and verify directory structure."""
        
        if not os.path.exists(problem_suite_path):
            error(f"[POLYGLOT] Problem suite directory not found: {problem_suite_path}")
            raise FileNotFoundError(f"Problem suite directory not found: {problem_suite_path}")
            
        json_path = os.path.join(problem_suite_path, "polyglot.json")
        if not os.path.exists(json_path):
            error(f"[POLYGLOT] polyglot.json not found at: {json_path}")
            raise FileNotFoundError(f"polyglot.json not found at: {json_path}")
            
        try:
            with open(json_path, "r") as f:
                problems_list = json.load(f)
            
            info(f"[POLYGLOT] Loaded {len(problems_list)} problems from {json_path}")
            
            # Process each problem
            for problem in problems_list:
                # Parse problem name
                problem_name = problem.get("name")
                if not problem_name:
                    error(f"[POLYGLOT] Found problem without name field")
                    raise ValueError("Found problem without name field")
                
                problem_dir = os.path.join(problem_suite_path, problem_name)
                
                # Verify directory exists
                if not os.path.exists(problem_dir):
                    error(f"[POLYGLOT]     Problem directory not found: {problem_name}")
                    raise FileNotFoundError(f"Problem directory not found: {problem_name}")
                    
                # Check for required files
                required_files = ["main.py", "tests.py", "instructions.md", "solution.py"]
                missing_files = []
                
                for required_file in required_files:
                    file_path = os.path.join(problem_dir, required_file)
                    if not os.path.exists(file_path):
                        missing_files.append(required_file)
                        
                if missing_files:
                    error(f"[POLYGLOT]     Problem {problem_name} missing files: {missing_files}")
                    raise FileNotFoundError(f"Problem {problem_name} missing files: {missing_files}")
                
                # Read problem statement from instructions.md
                instructions_path = os.path.join(problem_dir, "instructions.md")
                with open(instructions_path, "r") as f:
                    problem_statement = f.read()

                # Calculate diff between main.py and solution.py
                main_path = os.path.join(problem_dir, "main.py")
                solution_path = os.path.join(problem_dir, "solution.py")
                solution_diff = get_file_diff(main_path, solution_path)
                
                # Parse problem tests
                tests = problem.get("tests")
                if not tests:
                    error(f"[POLYGLOT]     Problem {problem_name} missing tests field")
                    raise ValueError(f"Problem {problem_name} missing tests field")
                
                self._add_problem(problem_name, problem_statement=problem_statement, solution_diff=solution_diff, tests=tests)
                
                debug(f"[POLYGLOT]     Problem {problem_name} verified successfully (found {len(tests)} associated tests)")
            
            info(f"[POLYGLOT] Successfully loaded {len(self.problems)} problems")
            
        except Exception as e:
            error(f"[POLYGLOT] Failed to load problems: {e}")
            raise e



    def copy_problem_files_to_directory(self, problem_name, dir, *, include_tests=False, include_solution=False):
        """Copy problem files to the given directory.""" 

        problem = self.get_problem(problem_name)
        if not problem:
            warn(f"[POLYGLOT] Problem {problem_name} not found")
            raise ValueError(f"Problem {problem_name} not found")

        problem_dir = os.path.join(self.problem_suite_path, problem_name)
        
        # Always copy main.py
        shutil.copy2(os.path.join(problem_dir, "main.py"), os.path.join(dir, "main.py"))
        debug(f"[POLYGLOT] Copied main.py to {dir} for {problem_name}")

        # Copy test files if requested
        if include_tests:
            shutil.copy2(os.path.join(problem_dir, "tests.py"), os.path.join(dir, "tests.py"))
            debug(f"[POLYGLOT] Copied tests.py to {dir} for {problem_name}")

        # Copy solution files if requested
        if include_solution:
            # Copy solution.py
            shutil.copy2(os.path.join(problem_dir, "solution.py"), os.path.join(dir, "solution.py"))
            debug(f"[POLYGLOT] Copied solution.py to {dir} for {problem_name}")
            
            # Write solution.diff file
            with open(os.path.join(dir, "solution.diff"), "w") as f:
                f.write(problem["solution_diff"])
            debug(f"[POLYGLOT] Created solution.diff in {dir} for {problem_name}")

        # Initialize git repository with initial commit
        debug(f"[POLYGLOT] Initializing git repository in {dir} for {problem_name}")
        success = init_repo_with_initial_commit(dir, "Initial commit")
        if success:
            debug(f"[POLYGLOT] Initialized git repository in {dir} for {problem_name}")
        else:
            warn(f"[POLYGLOT] Failed to initialize git repository in {dir} for {problem_name}")



    def get_test_runner_path(self):
        return os.path.join(os.path.dirname(__file__), "TEST_RUNNER.py")



    def get_problem_test_count(self, problem_name):
        problem = self.get_problem(problem_name)
        if not problem:
            return 0
        return len(problem.get("tests"))