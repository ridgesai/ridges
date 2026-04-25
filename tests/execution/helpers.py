import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harbor.models.agent.context import AgentContext
from harbor.models.task.id import LocalTaskId
from harbor.models.trial.config import TaskConfig, TrialConfig
from harbor.models.trial.result import AgentInfo, ExceptionInfo, TimingInfo, TrialResult
from harbor.models.verifier.result import VerifierResult

from ridges_harbor._stdlib_contract import (
    PATCH_APPLY_LOG_FILENAME,
    PATCH_CHECK_LOG_FILENAME,
    RUN_AGENT_PHASE,
    RUN_LOG_FILENAME,
    RUNTIME_LOG_FILENAME,
    RUNTIME_PAYLOAD_FILENAME,
    SETUP_LOG_FILENAME,
)
from ridges_harbor.runner import HarborRunSummary
from ridges_harbor.runtime_contract import (
    RidgesRuntimeFailure,
)


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def write_json(path: Path, payload: dict) -> None:
    write(path, json.dumps(payload))


def _timing_info(payload: dict[str, Any] | None) -> TimingInfo | None:
    if payload is None:
        return None
    return TimingInfo.model_validate(payload)


def _exception_info(payload: dict[str, Any] | None) -> ExceptionInfo | None:
    if payload is None:
        return None
    return ExceptionInfo(
        exception_type=str(payload.get("exception_type", "RuntimeError")),
        exception_message=str(payload.get("exception_message", "")),
        exception_traceback=str(payload.get("exception_traceback", "")),
        occurred_at=payload.get("occurred_at", datetime.now(timezone.utc)),
    )


def _runtime_payload(
    *,
    runtime_failure: dict[str, Any] | None,
    runtime_payload: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if runtime_payload is not None:
        return runtime_payload

    if runtime_failure is None:
        return None

    normalized_runtime_failure = {
        "phase": RUN_AGENT_PHASE,
        "traceback": "Traceback...",
        **runtime_failure,
    }
    failure = RidgesRuntimeFailure.model_validate(normalized_runtime_failure)
    return failure.model_dump(mode="json")


def make_summary(
    tmp_path: Path,
    *,
    exception_info: dict | None = None,
    runtime_failure: dict | None = None,
    runtime_payload: dict | None = None,
    patch: str | None = "--- a/status.txt\n+++ b/status.txt\n@@ -1 +1 @@\n-pending\n+done\n",
    test_results: dict | None = None,
    artifact_test_results: dict | None = None,
    report_payload: dict | None = None,
    artifact_report_payload: dict | None = None,
    eval_log: str | None = "primary eval log",
    run_log: str | None = "miner logs",
    runtime_log: str | None = None,
    trial_log: str | None = None,
    trial_exception_text: str | None = None,
    environment_setup: dict | None = None,
    agent_setup: dict | None = None,
    agent_execution: dict | None = None,
    verifier: dict | None = None,
    verifier_result: dict | None = None,
) -> HarborRunSummary:
    trial_dir = tmp_path / "trial"
    agent_dir = trial_dir / "agent"
    verifier_dir = trial_dir / "verifier"
    artifacts_dir = trial_dir / "artifacts"
    task_dir = tmp_path / "task"

    if patch is not None:
        write(agent_dir / "patch.diff", patch)

    if test_results is not None:
        write_json(verifier_dir / "test_results.json", test_results)

    if artifact_test_results is not None:
        write_json(artifacts_dir / "test_results.json", artifact_test_results)

    if report_payload is not None:
        write_json(verifier_dir / "report.json", report_payload)

    if artifact_report_payload is not None:
        write_json(artifacts_dir / "report.json", artifact_report_payload)

    if eval_log is not None:
        write(verifier_dir / "test-stdout.txt", eval_log)

    write(agent_dir / SETUP_LOG_FILENAME, "setup ok")
    if run_log is not None:
        write(agent_dir / RUN_LOG_FILENAME, run_log)

    if runtime_log is not None:
        write(agent_dir / RUNTIME_LOG_FILENAME, runtime_log)

    write(agent_dir / PATCH_CHECK_LOG_FILENAME, "git apply check")
    write(agent_dir / PATCH_APPLY_LOG_FILENAME, "git apply")

    if trial_log is not None:
        write(trial_dir / "trial.log", trial_log)

    if trial_exception_text is not None:
        write(trial_dir / "exception.txt", trial_exception_text)

    runtime_payload_data = _runtime_payload(
        runtime_failure=runtime_failure,
        runtime_payload=runtime_payload,
    )
    if runtime_payload_data is not None:
        write_json(agent_dir / RUNTIME_PAYLOAD_FILENAME, runtime_payload_data)

    agent_result = AgentContext(metadata={})
    parsed_verifier_result = VerifierResult.model_validate(verifier_result) if verifier_result is not None else None

    trial_result = TrialResult(
        task_name="update-status-file",
        trial_name="update-status-file__trial-1",
        trial_uri=trial_dir.resolve().as_uri(),
        task_id=LocalTaskId(path=task_dir),
        task_checksum="sha256:fake",
        config=TrialConfig(task=TaskConfig(path=task_dir)),
        agent_info=AgentInfo(name="ridges-miner", version="0.2.0"),
        agent_result=agent_result,
        verifier_result=parsed_verifier_result,
        exception_info=_exception_info(exception_info),
        environment_setup=_timing_info(environment_setup),
        agent_setup=_timing_info(agent_setup),
        agent_execution=_timing_info(agent_execution),
        verifier=_timing_info(verifier),
    )

    return HarborRunSummary(
        trial_result=trial_result,
        task_name="update-status-file",
        job_dir=tmp_path / "job",
        task_dir=task_dir,
        trial_dir=trial_dir,
    )


def successful_test_results() -> dict:
    return {
        "success": True,
        "output": [
            {
                "name": "status updated",
                "category": "default",
                "status": "pass",
            }
        ],
    }


def successful_verifier_result() -> dict:
    return {
        "rewards": {
            "reward": 1.0,
        }
    }


def successful_report_payload() -> dict:
    return {
        "update-status-file": {
            "tests_status": {
                "FAIL_TO_PASS": {
                    "success": ["status updated"],
                    "failure": [],
                },
                "PASS_TO_PASS": {
                    "success": ["regression check"],
                    "failure": [],
                },
                "FAIL_TO_FAIL": {
                    "success": ["known failing test"],
                    "failure": [],
                },
                "PASS_TO_FAIL": {
                    "success": [],
                    "failure": ["new regression"],
                },
            }
        }
    }


def timing(started_at: str = "2026-04-09T00:00:00Z", finished_at: str = "2026-04-09T00:00:01Z") -> dict:
    return {
        "started_at": started_at,
        "finished_at": finished_at,
    }


def valid_execution_spec() -> dict:
    return {
        "kind": "harbor_remote_task",
        "dataset_name": "test_dataset",
        "task_name": "update-status-file",
        "s3_key": "tasks/test_dataset/update-status-file.tar.gz",
        "task_digest": "sha256:fake",
        "agent_timeout_sec": 30.0,
        "benchmark_family": "test_dataset",
        "problem_suite_name": "test_dataset",
        "problem_difficulty": "easy",
    }
