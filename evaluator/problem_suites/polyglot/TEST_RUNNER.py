"""Polyglot Test Runner - Python Version

This script runs inside the Docker sandbox to execute Python unit tests
for the polyglot problem suite. It discovers and runs unittest-based tests
in the /sandbox/repo directory.

Execution Flow:
1. Loads the main.py module (the code being tested)
2. Loads the tests.py module (unittest test cases)
3. Discovers all test methods in the test class
4. Runs each test and collects results
5. Writes structured results to output.json

Expected File Layout:
    /sandbox/repo/
        ├── main.py    # Code to test (loaded as module)
        └── tests.py   # Unittest test cases

Output Format:
    [
        {"name": "test_method_1", "category": "default", "status": "pass|fail|skip"},
        ...
    ]

Note:
    - Tests run sequentially, not in parallel
    - All tests are discovered from a single TestCase subclass
    - Test execution continues even after failures
"""

import os
import sys
import json
import unittest
import traceback
import importlib.util



# Add the repository directory to Python path so imports work
repo_path = "/sandbox/repo"
sys.path.insert(0, repo_path)



def run_tests():
    """Discover and run all tests in the repository.
    
    Returns:
        List of test result dictionaries with name, category, and status
    """
    # Load the main module (the code being tested)
    # This makes the implementation available to the tests
    print("[POLYGLOT_TEST_RUNNER] Loading main.py")
    main_spec = importlib.util.spec_from_file_location("main", "/sandbox/repo/main.py")
    main_module = importlib.util.module_from_spec(main_spec)
    main_spec.loader.exec_module(main_module)
    print("[POLYGLOT_TEST_RUNNER] Loaded main.py")
    
    # Load tests module
    print("[POLYGLOT_TEST_RUNNER] Loading tests.py")
    tests_spec = importlib.util.spec_from_file_location("tests", "/sandbox/repo/tests.py")
    tests_module = importlib.util.module_from_spec(tests_spec)
    tests_spec.loader.exec_module(tests_module)
    print("[POLYGLOT_TEST_RUNNER] Loaded tests.py")
    
    test_class = None
    for name in dir(tests_module):
        obj = getattr(tests_module, name)
        if (isinstance(obj, type) and issubclass(obj, unittest.TestCase) and obj is not unittest.TestCase):
            test_class = obj
            break
    
    if not test_class:
        raise Exception("No test class found in tests.py")
    
    print(f"[POLYGLOT_TEST_RUNNER] Found test class: {test_class.__name__}")
    
    test_methods = [method for method in dir(test_class) if method.startswith("test_")]
    print(f"[POLYGLOT_TEST_RUNNER] Found {len(test_methods)} test methods")
    


    test_results = []
    for method_name in test_methods:
        test_results.append({"name": method_name, "category": "default", "status": "skip"})
    
    total_tests = len(test_results)
    test_instance = test_class()
    
    for test_index, test_result in enumerate(test_results, 1):
        method_name = test_result["name"]
        
        try:
            print(f"[POLYGLOT_TEST_RUNNER] [{test_index}/{total_tests}] Running {method_name}...")
            method = getattr(test_instance, method_name)
            method()
            print(f"[POLYGLOT_TEST_RUNNER] {method_name}: PASSED")
            test_result["status"] = "pass"
        except Exception as e:
            print(f"[POLYGLOT_TEST_RUNNER] {method_name}: FAILED - {e}")
            test_result["status"] = "fail"
            # break
    
    return test_results



def main():
    """Main entry point for test execution inside the sandbox."""
    print("[POLYGLOT_TEST_RUNNER] Entered main()")
    
    try:
        # Run all tests and collect results
        test_results = run_tests()

        # Print results for debugging (visible in container logs)
        print("[POLYGLOT_TEST_RUNNER] Test results:")
        print(json.dumps(test_results, indent=2))
        
        tests_passed = sum(1 for test in test_results if test["status"] == "pass")
        tests_failed = sum(1 for test in test_results if test["status"] == "fail")
        tests_skipped = sum(1 for test in test_results if test["status"] == "skip")
        
        print(f"[POLYGLOT_TEST_RUNNER] Test summary: {tests_passed} passed, {tests_failed} failed, {tests_skipped} skipped")

        print("[POLYGLOT_TEST_RUNNER] Writing output.json")
        with open("/sandbox/output.json", "w") as f:
            json.dump({
                "success": True,
                "output": test_results
            }, f, indent=2)
        print("[POLYGLOT_TEST_RUNNER] Wrote output.json")

    except Exception as e:
        print("[POLYGLOT_TEST_RUNNER] Exception:")
        traceback.print_exc(file=sys.stdout)
        
        output = {
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }
        
        try:
            print("[POLYGLOT_TEST_RUNNER] Writing output.json")
            with open("/sandbox/output.json", "w") as f:
                json.dump(output, f, indent=2)
            print("[POLYGLOT_TEST_RUNNER] Wrote output.json")
        except:
            print("[POLYGLOT_TEST_RUNNER] Failed to write output.json")
            pass

    print("[POLYGLOT_TEST_RUNNER] Exiting main()")



if __name__ == "__main__":
    main()