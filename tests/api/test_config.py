import os
import subprocess
import sys
from pathlib import Path


def test_netuid_defaults_to_62_when_environment_value_is_missing() -> None:
    env = os.environ.copy()
    env.pop("NETUID", None)
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import dotenv; dotenv.load_dotenv = lambda: False; import api.config; print(api.config.NETUID)",
        ],
        cwd=repo_root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "62"


def test_projector_loops_follow_deployment_loop_switch_not_policy_defaults() -> None:
    env = os.environ.copy()
    env.update(
        {
            "SHOULD_RUN_LOOPS": "true",
            "PRE_SCREENING_JUDGE_ENABLED": "false",
            "AUTO_APPROVAL_ENABLED": "false",
        }
    )
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import dotenv; dotenv.load_dotenv = lambda: False; import api.config as c; "
                "print(c.PRE_SCREENING_PROJECTOR_RUN_LOOP, c.AUTO_APPROVAL_RUN_LOOP)"
            ),
        ],
        cwd=repo_root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "True True"
