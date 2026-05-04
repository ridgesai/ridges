from pathlib import Path

import pytest

from execution.artifacts import read_trial_snapshot, result_from_summary
from execution.errors import EvaluationRunException
from models.evaluation_run import EvaluationRunErrorCode
from ridges_harbor._stdlib_contract import (
    PATCH_APPLY_LOG_FILENAME,
    RUNTIME_PAYLOAD_FILENAME,
    SETUP_LOG_FILENAME,
)

from .helpers import (
    make_summary,
    successful_report_payload,
    successful_test_results,
    successful_verifier_result,
    write,
    write_json,
)


def test_happy_path_returns_execution_result(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        test_results=successful_test_results(),
        eval_log="verifier output",
        verifier_result=successful_verifier_result(),
    )

    result = result_from_summary(summary)

    assert result.backend == "harbor"
    assert result.patch == "--- a/status.txt\n+++ b/status.txt\n@@ -1 +1 @@\n-pending\n+done\n"
    assert result.verifier_reward == 1.0
    assert len(result.test_results) == 1
    assert result.test_results[0].name == "status updated"
    assert result.agent_logs.startswith(f"# {SETUP_LOG_FILENAME}\nsetup ok")
    assert f"# {RUNTIME_PAYLOAD_FILENAME}" not in result.agent_logs
    assert result.eval_logs == "verifier output"


def test_read_trial_snapshot_returns_patch_and_surfaced_agent_logs(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        test_results=successful_test_results(),
        verifier_result=successful_verifier_result(),
        runtime_log="runtime output",
        trial_log="trial output",
    )

    snapshot = read_trial_snapshot(summary.trial_dir)

    assert snapshot.patch == "--- a/status.txt\n+++ b/status.txt\n@@ -1 +1 @@\n-pending\n+done\n"
    assert snapshot.agent_logs.startswith(f"# {SETUP_LOG_FILENAME}\nsetup ok")
    assert "# runtime.log\nruntime output" in snapshot.agent_logs
    assert "# trial.log\ntrial output" in snapshot.agent_logs


def test_report_json_fills_structured_test_results_when_legacy_file_is_missing(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        report_payload=successful_report_payload(),
        eval_log="verifier output",
        verifier_result=successful_verifier_result(),
    )

    result = result_from_summary(summary)

    assert len(result.test_results) == 2
    assert {test.name for test in result.test_results} == {"status updated", "regression check"}
    assert {test.status.value for test in result.test_results} == {"pass"}
    assert {test.category.value for test in result.test_results} == {"fail_to_pass", "pass_to_pass"}


def test_zero_reward_returns_execution_result_with_report_failures(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        report_payload={
            "update-status-file": {
                "tests_status": {
                    "FAIL_TO_PASS": {
                        "success": [],
                        "failure": ["status updated"],
                    },
                    "PASS_TO_PASS": {
                        "success": ["regression check"],
                        "failure": [],
                    },
                }
            }
        },
        verifier_result={"rewards": {"reward": 0.0}},
    )

    result = result_from_summary(summary)

    assert result.patch
    assert result.verifier_reward == 0.0
    assert [(test.name, test.status.value) for test in result.test_results] == [
        ("status updated", "fail"),
        ("regression check", "pass"),
    ]


def test_fractional_reward_returns_execution_result(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        test_results=successful_test_results(),
        verifier_result={"rewards": {"reward": 0.8}},
    )

    result = result_from_summary(summary)

    assert result.patch
    assert result.verifier_reward == 0.8
    assert len(result.test_results) == 1


def test_runtime_log_is_included_in_agent_logs(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        exception_info={
            "exception_type": "MinerInvalidPatchError",
            "exception_message": "Miner returned an invalid patch",
            "exception_traceback": "Traceback...\nridges_harbor/agents.py\n",
        },
        test_results=successful_test_results(),
        run_log="[state] cancelled",
        runtime_log="live runtime output",
    )

    with pytest.raises(EvaluationRunException) as exc_info:
        result_from_summary(summary)

    assert "live runtime output" in exc_info.value.extra["agent_logs"]


def test_agent_timeout_surfaces_runtime_log_when_run_log_only_records_cancellation(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        exception_info={
            "exception_type": "AgentTimeoutError",
            "exception_message": "Agent execution timed out after 30 seconds",
            "exception_traceback": "Traceback...\n_execute_agent\n",
        },
        agent_execution={
            "started_at": "2026-04-09T00:00:00Z",
            "finished_at": "2026-04-09T00:00:30Z",
        },
        run_log="[state] started\n[state] cancelled\n[detail] agent execution was cancelled, likely due to timeout",
        runtime_log="streamed stdout before timeout",
    )

    with pytest.raises(EvaluationRunException) as exc_info:
        result_from_summary(summary)

    assert exc_info.value.error_code == EvaluationRunErrorCode.AGENT_TIMEOUT_RUNNING_AGENT
    assert "streamed stdout before timeout" in exc_info.value.extra["agent_logs"]
    assert "[state] cancelled" in exc_info.value.extra["agent_logs"]


def test_reward_only_success_without_test_results_returns_empty_list(tmp_path: Path) -> None:
    summary = make_summary(tmp_path, test_results=None, verifier_result=successful_verifier_result())

    result = result_from_summary(summary)

    assert result.patch
    assert result.verifier_reward == 1.0
    assert result.test_results == []


def test_missing_patch_artifact_maps_to_validator_internal_error(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        patch=None,
        test_results=successful_test_results(),
        verifier_result=successful_verifier_result(),
    )

    with pytest.raises(EvaluationRunException) as exc_info:
        result_from_summary(summary)

    assert exc_info.value.error_code == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR
    assert "patch artifact" in exc_info.value.error_message.lower()


def test_classified_failure_omits_empty_eval_logs_from_extra(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        patch=None,
        test_results=successful_test_results(),
        eval_log=None,
        verifier_result=successful_verifier_result(),
    )

    with pytest.raises(EvaluationRunException) as exc_info:
        result_from_summary(summary)

    assert exc_info.value.error_code == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR
    assert "agent_logs" in exc_info.value.extra
    assert "eval_logs" not in exc_info.value.extra
    assert exc_info.value.extra["job_dir"] == summary.job_dir


def test_artifact_test_results_fallback_is_used(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        test_results=None,
        artifact_test_results=successful_test_results(),
        verifier_result=successful_verifier_result(),
    )

    result = result_from_summary(summary)

    assert len(result.test_results) == 1
    assert result.test_results[0].name == "status updated"


def test_artifact_report_fallback_is_used(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        test_results=None,
        report_payload=None,
        artifact_report_payload=successful_report_payload(),
        verifier_result=successful_verifier_result(),
    )

    result = result_from_summary(summary)

    assert len(result.test_results) == 2
    assert {test.name for test in result.test_results} == {"status updated", "regression check"}


def test_trial_log_is_appended_to_agent_logs(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        test_results=successful_test_results(),
        trial_log="harbor trial log",
        verifier_result=successful_verifier_result(),
    )

    result = result_from_summary(summary)

    assert "# trial.log\nharbor trial log" in result.agent_logs
    assert result.agent_logs.index("# trial.log\nharbor trial log") > result.agent_logs.index(
        f"# {PATCH_APPLY_LOG_FILENAME}"
    )


def test_exception_file_is_appended_to_agent_logs(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        test_results=successful_test_results(),
        trial_exception_text="harbor exception traceback",
        verifier_result=successful_verifier_result(),
    )

    result = result_from_summary(summary)

    assert "# exception.txt\nharbor exception traceback" in result.agent_logs
    assert result.agent_logs.index("# exception.txt\nharbor exception traceback") > result.agent_logs.index(
        f"# {PATCH_APPLY_LOG_FILENAME}"
    )


def test_non_object_verifier_payload_maps_to_validator_internal_error(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        patch="valid patch",
        test_results=None,
        verifier_result=successful_verifier_result(),
    )
    write(summary.trial_dir / "verifier" / "test_results.json", "[]")

    with pytest.raises(EvaluationRunException) as exc_info:
        result_from_summary(summary)

    assert exc_info.value.error_code == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR
    assert "valid dictionary" in exc_info.value.error_message


def test_non_list_verifier_output_maps_to_validator_internal_error(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        patch="valid patch",
        verifier_result=successful_verifier_result(),
        test_results={
            "success": True,
            "output": {"name": "status updated"},
        },
    )

    with pytest.raises(EvaluationRunException) as exc_info:
        result_from_summary(summary)

    assert exc_info.value.error_code == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR
    assert "output" in exc_info.value.error_message
    assert "valid list" in exc_info.value.error_message


def test_non_boolean_verifier_success_maps_to_validator_internal_error(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        patch="valid patch",
        verifier_result=successful_verifier_result(),
        test_results={"success": []},
    )

    with pytest.raises(EvaluationRunException) as exc_info:
        result_from_summary(summary)

    assert exc_info.value.error_code == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR
    assert "success" in exc_info.value.error_message
    assert "valid boolean" in exc_info.value.error_message


def test_non_string_verifier_error_maps_to_validator_internal_error(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        patch="valid patch",
        verifier_result=successful_verifier_result(),
        test_results={"success": False, "error": []},
    )

    with pytest.raises(EvaluationRunException) as exc_info:
        result_from_summary(summary)

    assert exc_info.value.error_code == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR
    assert "error" in exc_info.value.error_message
    assert "valid string" in exc_info.value.error_message


def test_invalid_verifier_output_item_maps_to_validator_internal_error(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        patch="valid patch",
        verifier_result=successful_verifier_result(),
        test_results={"success": True, "output": [{"name": "status updated"}]},
    )

    with pytest.raises(EvaluationRunException) as exc_info:
        result_from_summary(summary)

    assert exc_info.value.error_code == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR
    assert "output.0.category" in exc_info.value.error_message
    assert "Field required" in exc_info.value.error_message


def test_multi_metric_reward_payload_maps_to_validator_internal_error(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        verifier_result={"rewards": {"score": 1.0, "secondary": 0.5}},
    )

    with pytest.raises(EvaluationRunException) as exc_info:
        result_from_summary(summary)

    assert exc_info.value.error_code == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR
    assert "multi-metric reward payload" in exc_info.value.error_message


def test_reward_key_is_preferred_even_if_other_metrics_are_present(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        verifier_result={"rewards": {"reward": 1.0, "secondary": 0.5}},
    )

    result = result_from_summary(summary)

    assert result.patch
    assert result.verifier_reward == 1.0
    assert result.test_results == []


def test_single_nonstandard_reward_metric_is_accepted(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        verifier_result={"rewards": {"score": 1.0}},
    )

    result = result_from_summary(summary)

    assert result.patch
    assert result.verifier_reward == 1.0
    assert result.test_results == []


def test_empty_reward_payload_maps_to_validator_internal_error(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        verifier_result={"rewards": {}},
    )

    with pytest.raises(EvaluationRunException) as exc_info:
        result_from_summary(summary)

    assert exc_info.value.error_code == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR
    assert "usable reward payload" in exc_info.value.error_message


def test_best_effort_verifier_report_is_appended_to_eval_logs(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        eval_log="verifier output",
        verifier_result=successful_verifier_result(),
    )
    write_json(
        summary.trial_dir / "verifier" / "report.json",
        {"suite": "swebench", "passed": ["case_a"], "failed": []},
    )

    result = result_from_summary(summary)

    assert "verifier output" in result.eval_logs
    assert "discovered_verifier_report" in result.eval_logs
    assert "verifier/report.json" in result.eval_logs
    assert '"suite": "swebench"' in result.eval_logs


def test_missing_verifier_result_maps_to_validator_internal_error(tmp_path: Path) -> None:
    summary = make_summary(tmp_path, verifier_result=None)

    with pytest.raises(EvaluationRunException) as exc_info:
        result_from_summary(summary)

    assert exc_info.value.error_code == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR
    assert "without a verifier result" in exc_info.value.error_message
