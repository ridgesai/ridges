# REMOVED: Falling back to SWE-Bench Harness

# import sys
# import json
# import traceback
# import subprocess
# import importlib.util
# import time



# def categorize_test(test_name):
#     """
#     pytest: "path/to/test_file.py::test_function[params]"
    
#     django: "method (directory.file.Class)" |
#             "method (directory.file.Class.method)"
#     """
#     import re

#     if "::" in test_name:
#         return "pytest"
#     elif re.match(r'^test_\w+ \([a-zA-Z_][a-zA-Z0-9_.]*\.[A-Z][a-zA-Z0-9_]*\)$', test_name):
#         return "django"
#     elif re.match(r'^test_\w+ \([a-zA-Z_][a-zA-Z0-9_.]*\.[A-Z][a-zA-Z0-9_]*\.test_\w+\)$', test_name):
#         return "django"
#     else:
#         return None



# def run_test(test_name, test_type, test_index, total_tests):
#     print(f"[SWEBENCH_TEST_RUNNER] [{test_index}/{total_tests}] Running {test_type} test: {test_name}")
    
#     if test_type == "django":
#         test_method, test_class = test_name.split(" (", 1)
#         test_class = test_class.rstrip(")")
        
#         # method (directory.file.Class.method)
#         if test_class.endswith(f".{test_method}"):
#             test_class = test_class[:-len(f".{test_method}")]
        
#         django_test = f"{test_class}.{test_method}"
        
#         result = subprocess.run(
#             f"python -m pip install -e .. && python runtests.py {django_test} --verbosity=2",
#             shell=True,
#             capture_output=True,
#             text=True,
#             cwd="/sandbox/tests"
#         )
        
#         if result.returncode != 0:
#             print(f"[SWEBENCH_TEST_RUNNER] TEST FAILED!")
#             if result.stdout:
#                 print(f"[SWEBENCH_TEST_RUNNER] STDOUT:\n{result.stdout}")
#             if result.stderr:
#                 print(f"[SWEBENCH_TEST_RUNNER] STDERR:\n{result.stderr}")
        
#         return result.returncode == 0

#     # TODO
 
#     return True



# def analyze_tests(tests, test_type_label):
#     """Analyze tests and return structured list with metadata."""

#     pytest_tests = []
#     django_tests = []
#     unknown_tests = []
    
#     for test in tests:
#         format_type = categorize_test(test)
#         if format_type == "pytest":
#             pytest_tests.append(test)
#         elif format_type == "django":
#             django_tests.append(test)
#         else:
#             unknown_tests.append(test)
    
#     print(f"[SWEBENCH_TEST_RUNNER] Total number of {test_type_label} tests: {len(tests)}")
    
#     if django_tests:
#         print(f"[SWEBENCH_TEST_RUNNER]      django tests: {len(django_tests)}")
#         for test in django_tests:
#             print(f"[SWEBENCH_TEST_RUNNER]        {test}")
#     if pytest_tests:
#         print(f"[SWEBENCH_TEST_RUNNER]      pytest tests: {len(pytest_tests)}")
#         for test in pytest_tests:
#             print(f"[SWEBENCH_TEST_RUNNER]        {test}")
#     if unknown_tests:
#         print(f"[SWEBENCH_TEST_RUNNER]      unknown tests: {len(unknown_tests)}")
#         for test in unknown_tests:
#             print(f"[SWEBENCH_TEST_RUNNER]        {test}")
    
#     test_results = []
#     for test in pytest_tests:
#         test_results.append({"name": test, "category": test_type_label, "type": "pytest", "status": "skip"})
#     for test in django_tests:
#         test_results.append({"name": test, "category": test_type_label, "type": "django", "status": "skip"})
    
#     return test_results



# def run_tests(tests):
#     fail_to_pass_list = analyze_tests(tests.get('fail_to_pass', []), "fail_to_pass")
#     print("[SWEBENCH_TEST_RUNNER] ====================================================================================================")
#     pass_to_pass_list = analyze_tests(tests.get('pass_to_pass', []), "pass_to_pass")
#     print("[SWEBENCH_TEST_RUNNER] ====================================================================================================")

#     all_test_results = fail_to_pass_list + pass_to_pass_list
#     total_tests = len(all_test_results)
    
#     tests_passed = 0
#     tests_failed = 0
    
#     for test_index, test_result in enumerate(all_test_results, 1):
#         test_name = test_result["name"]
#         test_type = test_result["type"]
        
#         test_passed = run_test(test_name, test_type, test_index, total_tests)
        
#         if test_passed:
#             test_result["status"] = "pass"
#             tests_passed += 1
#         else:
#             test_result["status"] = "fail"
#             tests_failed += 1
#             break
    
#     return all_test_results



# def main():
#     print("[SWEBENCH_TEST_RUNNER] Entered main()")
    
#     try:
#         print("[SWEBENCH_TEST_RUNNER] Reading input.json")
#         with open("/sandbox/input.json", "r") as f:
#             input_data = json.load(f)
#         print("[SWEBENCH_TEST_RUNNER] Read input.json")

#         print("[SWEBENCH_TEST_RUNNER] ====================================================================================================")
#         test_results = run_tests(input_data["tests"])
#         print("[SWEBENCH_TEST_RUNNER] ====================================================================================================")
        
#         tests_passed = sum(1 for test in test_results if test["status"] == "pass")
#         tests_failed = sum(1 for test in test_results if test["status"] == "fail")
#         tests_skipped = sum(1 for test in test_results if test["status"] == "skip")
        
#         print(f"[SWEBENCH_TEST_RUNNER] Test summary: {tests_passed} passed, {tests_failed} failed, {tests_skipped} skipped")

#         print("[SWEBENCH_TEST_RUNNER] Writing output.json")
#         with open("/sandbox/output.json", "w") as f:
#             json.dump({"status": "success", "output": test_results}, f, indent=2)
#         print("[SWEBENCH_TEST_RUNNER] Wrote output.json")

#     except Exception as e:
#         print("[SWEBENCH_TEST_RUNNER] Exception:")
#         traceback.print_exc(file=sys.stdout)
        
#         output = {
#             "status": "error",
#             "error": str(e),
#             "traceback": traceback.format_exc()
#         }
        
#         try:
#             print("[SWEBENCH_TEST_RUNNER] Writing output.json")
#             with open("/sandbox/output.json", "w") as f:
#                 json.dump(output, f, indent=2)
#             print("[SWEBENCH_TEST_RUNNER] Wrote output.json")
#         except:
#             print("[SWEBENCH_TEST_RUNNER] Failed to write output.json")
#             pass
 
#     # while True:
#     #     time.sleep(60)



# if __name__ == "__main__":
#     main()