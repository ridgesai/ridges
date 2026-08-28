import os
from pathlib import Path

import pytest

from execution.artifacts import result_from_summary
from execution.errors import EvaluationRunException
from models.evaluation_run import EvaluationRunErrorCode
from ridges_harbor.digest import compute_task_digest
from ridges_harbor.runner import run_task

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_HARBOR_DOCKER_INTEGRATION") != "1",
    reason="set RUN_HARBOR_DOCKER_INTEGRATION=1 to run native Harbor Docker integration tests",
)


def _write_native_separate_task(task_dir: Path) -> None:
    task_dir.mkdir()
    (task_dir / "instruction.md").write_text("Change status.txt from pending to done.\n")
    (task_dir / "task.toml").write_text(
        'schema_version = "1.3"\n'
        'artifacts = ["/logs/agent/patch.diff"]\n'
        "\n"
        "[environment]\n"
        'workdir = "/app"\n'
        "\n"
        "[agent]\n"
        'user = "root"\n'
        "\n"
        "[verifier]\n"
        'environment_mode = "separate"\n'
        "\n"
        "[verifier.environment]\n"
        'workdir = "/app"\n'
    )

    environment_dir = task_dir / "environment"
    environment_dir.mkdir()
    (environment_dir / "Dockerfile").write_text(
        "FROM python:3.13-alpine\nRUN apk add --no-cache bash git\nWORKDIR /app\nRUN printf 'pending\\n' > status.txt\n"
    )
    (environment_dir / "docker-compose.yaml").write_text(
        "services:\n"
        "  main:\n"
        "    labels:\n"
        '      ridges.trial_id: "${RIDGES_TRIAL_ID}"\n'
        "    networks:\n"
        "      - sandbox_internal\n"
        "  sandbox-proxy:\n"
        "    image: alpine:3.22\n"
        '    command: ["sh", "-c", "sleep 600"]\n'
        "    networks:\n"
        "      - sandbox_internal\n"
        "      - sandbox_egress\n"
        "networks:\n"
        "  sandbox_internal:\n"
        "    internal: true\n"
        "  sandbox_egress:\n"
        "    labels:\n"
        '      ridges.trial_id: "${RIDGES_TRIAL_ID}"\n'
    )

    tests_dir = task_dir / "tests"
    tests_dir.mkdir()
    (tests_dir / "Dockerfile").write_text(
        "FROM python:3.13-alpine\n"
        "RUN apk add --no-cache bash git\n"
        "WORKDIR /app\n"
        "RUN printf 'pending\\n' > status.txt\n"
        "COPY test.sh hidden-canary.txt /tests/\n"
    )
    (tests_dir / "hidden-canary.txt").write_text("visible only in the verifier image\n")
    (tests_dir / "test.sh").write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "mkdir -p /logs/verifier\n"
        "cd /app\n"
        'if [ -f /tests/hidden-canary.txt ] && [ "$(cat status.txt)" = done ]; then\n'
        "  printf '1\\n' > /logs/verifier/reward.txt\n"
        "else\n"
        "  printf '0\\n' > /logs/verifier/reward.txt\n"
        "fi\n"
    )


def _write_agent(agent_path: Path) -> None:
    agent_path.write_text(
        "import os\n"
        "from pathlib import Path\n"
        "\n"
        "def agent_main(_input):\n"
        "    if os.geteuid() != 0:\n"
        "        raise RuntimeError('separate miner did not run as root')\n"
        "    if Path('/tests/hidden-canary.txt').exists():\n"
        "        raise RuntimeError('hidden verifier files leaked into the agent environment')\n"
        "    return (\n"
        "        'diff --git a/status.txt b/status.txt\\n'\n"
        "        '--- a/status.txt\\n'\n"
        "        '+++ b/status.txt\\n'\n"
        "        '@@ -1 +1 @@\\n'\n"
        "        '-pending\\n'\n"
        "        '+done\\n'\n"
        "    )\n"
    )


def _write_timeout_artifact_agent(agent_path: Path) -> None:
    agent_path.write_text(
        "import time\n"
        "from pathlib import Path\n"
        "\n"
        "PATCH = (\n"
        "    'diff --git a/status.txt b/status.txt\\n'\n"
        "    '--- a/status.txt\\n'\n"
        "    '+++ b/status.txt\\n'\n"
        "    '@@ -1 +1 @@\\n'\n"
        "    '-pending\\n'\n"
        "    '+done\\n'\n"
        ")\n"
        "\n"
        "def agent_main(_input):\n"
        "    Path('/logs/agent/patch.diff').write_text(PATCH)\n"
        "    time.sleep(10)\n"
        "    return PATCH\n"
    )


def _write_native_shared_task(task_dir: Path) -> None:
    task_dir.mkdir()
    (task_dir / "instruction.md").write_text("Change status.txt from pending to done.\n")
    (task_dir / "task.toml").write_text('schema_version = "1.3"\n\n[environment]\nworkdir = "/app"\n')
    environment_dir = task_dir / "environment"
    environment_dir.mkdir()
    (environment_dir / "Dockerfile").write_text(
        "FROM python:3.13-alpine\nRUN apk add --no-cache bash git\nWORKDIR /app\nRUN printf 'pending\\n' > status.txt\n"
    )
    (environment_dir / "docker-compose.yaml").write_text(
        "services:\n"
        "  main:\n"
        "    labels:\n"
        '      ridges.trial_id: "${RIDGES_TRIAL_ID}"\n'
        "    networks:\n"
        "      - sandbox_internal\n"
        "  sandbox-proxy:\n"
        "    image: alpine:3.22\n"
        '    command: ["sh", "-c", "sleep 600"]\n'
        "    networks:\n"
        "      - sandbox_internal\n"
        "      - sandbox_egress\n"
        "networks:\n"
        "  sandbox_internal:\n"
        "    internal: true\n"
        "  sandbox_egress:\n"
        "    labels:\n"
        '      ridges.trial_id: "${RIDGES_TRIAL_ID}"\n'
    )
    tests_dir = task_dir / "tests"
    tests_dir.mkdir()
    (tests_dir / "test.sh").write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        'if [ "$(cat /app/status.txt)" = done ]; then\n'
        "  printf '1\\n' > /logs/verifier/reward.txt\n"
        "else\n"
        "  printf '0\\n' > /logs/verifier/reward.txt\n"
        "fi\n"
    )


@pytest.mark.anyio
async def test_native_shared_verifier_keeps_existing_in_place_flow(tmp_path: Path) -> None:
    task_dir = tmp_path / "native-shared-task"
    agent_path = tmp_path / "agent.py"
    _write_native_shared_task(task_dir)
    _write_agent(agent_path)

    summary = await run_task(
        task_dir,
        task_name="native-shared-task",
        task_digest=compute_task_digest(task_dir),
        evaluation_run_id="native-shared-integration",
        agent_path=agent_path,
        agent_timeout_sec=120,
        verifier_timeout_sec=120,
        results_dir=tmp_path / "results",
        job_name="native-shared-integration",
    )

    assert summary.trial_result.exception_info is None
    assert summary.trial_result.verifier_result is not None
    assert summary.trial_result.verifier_result.rewards == {"reward": 1.0}
    assert (summary.trial_dir / "agent" / "patch.diff").read_text().endswith("+done\n")


@pytest.mark.anyio
async def test_native_separate_verifier_hides_tests_and_grades_transferred_patch(tmp_path: Path) -> None:
    task_dir = tmp_path / "native-separate-task"
    agent_path = tmp_path / "agent.py"
    results_dir = tmp_path / "results"
    _write_native_separate_task(task_dir)
    _write_agent(agent_path)

    summary = await run_task(
        task_dir,
        task_name="native-separate-task",
        task_digest=compute_task_digest(task_dir),
        evaluation_run_id="native-separate-integration",
        agent_path=agent_path,
        agent_timeout_sec=120,
        verifier_timeout_sec=120,
        results_dir=results_dir,
        job_name="native-separate-integration",
    )

    assert summary.trial_result.exception_info is None
    assert summary.trial_result.verifier_result is not None
    assert summary.trial_result.verifier_result.rewards == {"reward": 1.0}
    assert (summary.trial_dir / "verifier" / "graded.patch").read_text().endswith("+done\n")


@pytest.mark.anyio
async def test_native_separate_rejects_agent_written_patch_after_timeout(tmp_path: Path) -> None:
    task_dir = tmp_path / "native-separate-timeout-task"
    agent_path = tmp_path / "timeout-agent.py"
    _write_native_separate_task(task_dir)
    _write_timeout_artifact_agent(agent_path)

    summary = await run_task(
        task_dir,
        task_name="native-separate-timeout-task",
        task_digest=compute_task_digest(task_dir),
        evaluation_run_id="native-separate-timeout-integration",
        agent_path=agent_path,
        agent_timeout_sec=1,
        verifier_timeout_sec=120,
        results_dir=tmp_path / "results",
        job_name="native-separate-timeout-integration",
    )

    assert summary.trial_result.exception_info is not None
    assert summary.trial_result.exception_info.exception_type == "AgentTimeoutError"
    assert summary.trial_result.verifier_result is not None
    assert summary.trial_result.verifier_result.rewards == {"reward": 1.0}

    with pytest.raises(EvaluationRunException) as exc_info:
        result_from_summary(summary)

    assert exc_info.value.error_code == EvaluationRunErrorCode.AGENT_TIMEOUT_RUNNING_AGENT
