import json
from typing import Dict, List

from sweagent.environment.swe_env import EnvironmentArguments, SWEEnv  

def run_tests(env: SWEEnv) -> Dict[str, str]:
    """
    Runs tests in the given environment and returns the results.

    Returns:
        Dict[str, str]: A dictionary with test names as keys and their status (passed, failed) as values.
    """
    try:
        env.communicate("pip install pytest-json-report")
        env.communicate("pytest --json-report --json-report-file=/tmp/report.json --json-report-omit collector", timeout_duration=300)
        pytest_report = env.communicate("cat /tmp/report.json")
        data = json.loads(pytest_report)

        tests = {}
        for test in data["tests"]:
            if test["outcome"] in ["passed", "failed"]:
                tests[test["nodeid"]] = test["outcome"].lower()
        
        return tests
    except Exception as e:
        print(f"Error running tests: {e}")
        return None

def apply_patch(env: SWEEnv, patch: str) -> bool:
    """
    Applies the given patch to the environment.

    Args:
        env (SWEEnv): The environment to apply the patch to.
        patch (str): The patch to apply.
    """
    try:
        env.communicate_with_handling(f"echo '{patch}' > /root/patch.patch", error_msg="Error writing patch")
        env.communicate_with_handling("git apply /root/patch.patch", error_msg="Error applying patch")
        return True
    except Exception as e:
        print(f"Error applying patch: {e}")
        return False
    
def compare_test_results(before: Dict[str, str], after: Dict[str, str]) -> Dict[str, List[str]]:
    """Compare test results before and after patches are applied."""
    pass_before = set()
    fail_before = set()
    pass_after = set()
    fail_after = set()

    for test, status in before.items():
        if status == "passed":
            pass_before.add(test)
        elif status == "failed":
            fail_before.add(test)
    for test, status in after.items():
        if status == "passed":
            pass_after.add(test)
        elif status == "failed":
            fail_after.add(test)

    return {
        "PASS_TO_PASS": list(pass_before & pass_after),
        "PASS_TO_FAIL": list(pass_before & fail_after),
        "FAIL_TO_PASS": list(fail_before & pass_after),
        "FAIL_TO_FAIL": list(fail_before & fail_after),
        "NEW_PASS": list(pass_after - pass_before - fail_before),
        "NEW_FAIL": list(fail_after - pass_before - fail_before),
    }