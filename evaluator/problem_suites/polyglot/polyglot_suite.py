"""The Polyglot problem suite."""

import os
import json
import shutil
import utils.logger as logger

from utils.diff import get_file_diff
from utils.git import init_local_repo_with_initial_commit
from evaluator.problem_suites.problem_suite import ProblemSuite


class PolyglotSuite(ProblemSuite):
    def __init__(self, dataset_path):
        super().__init__(dataset_path)



    def load_problems(self, dataset_path):
        """Load problems from polyglot.json and verify directory structure."""
        


        logger.info(f"Loading problems from {dataset_path}...")

        # Make sure the dataset path exists
        if not os.path.exists(dataset_path):
            logger.fatal(f"Dataset not found: {dataset_path}")
            
        # Make sure the polyglot.json file exists
        json_path = os.path.join(dataset_path, "polyglot.json")
        if not os.path.exists(json_path):
            logger.fatal(f"polyglot.json not found at: {json_path}")
            
        # Open the polyglot.json file
        with open(json_path, "r") as f:
            problems_list = json.load(f)
        
        logger.debug(f"Loaded {len(problems_list)} problems from {json_path}")
        
        # Process each problem
        for problem in problems_list:
            # Parse problem name
            problem_name = problem.get("name")
            if not problem_name:
                logger.fatal(f"Found problem without name field")
            
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

            # Calculate diff between main.py and solution.py
            main_path = os.path.join(problem_dir, "main.py")
            solution_path = os.path.join(problem_dir, "solution.py")
            solution_diff = get_file_diff(main_path, solution_path)
            
            # Parse problem tests
            tests = problem.get("tests")
            if not tests:
                logger.fatal(f"Problem {problem_name} missing tests field")
            
            # Add the problem to the suite
            self._add_problem(
                problem_name,
                problem_statement=problem_statement,
                solution_diff=solution_diff,
                tests=tests
            )
            
            logger.debug(f"Problem {problem_name} verified successfully")
        
        logger.info(f"Successfully loaded {len(self.problems)} problems from {dataset_path}")



    def copy_problem_files_to_directory(self, problem_name, dir, *, include_tests=False, include_solution=False):
        """Copy problem files to the given directory.""" 



        # Make sure the problem exists
        problem = self.get_problem(problem_name)
        if not problem:
            logger.fatal(f"Problem {problem_name} not found")

        problem_dir = os.path.join(self.dataset_path, problem_name)
        
        # Always copy main.py
        shutil.copy2(os.path.join(problem_dir, "main.py"), os.path.join(dir, "main.py"))
        logger.debug(f"Copied main.py to {dir} for {problem_name}")

        # Copy test files if requested
        if include_tests:
            shutil.copy2(os.path.join(problem_dir, "tests.py"), os.path.join(dir, "tests.py"))
            logger.debug(f"Copied tests.py to {dir} for {problem_name}")

        # Copy solution files if requested
        if include_solution:
            # Copy solution.py
            shutil.copy2(os.path.join(problem_dir, "solution.py"), os.path.join(dir, "solution.py"))
            logger.debug(f"Copied solution.py to {dir} for {problem_name}")
            
            # Write solution.diff file
            with open(os.path.join(dir, "solution.diff"), "w") as f:
                f.write(problem["solution_diff"])
            logger.debug(f"Created solution.diff in {dir} for {problem_name}")

        # Initialize git repository with initial commit
        init_local_repo_with_initial_commit(dir, "Initial commit")