from pathlib import Path

import pytest

from execution.artifacts import result_from_summary
from execution.errors import EvaluationRunException
from execution.failure_classifier import (
    InvalidRuntimePayloadError,
    extract_runtime_failure,
    looks_like_runtime_timeout,
    looks_like_runtime_transport_error,
    map_runtime_failure_code,
)
from models.evaluation_run import EvaluationRunErrorCode
from ridges_harbor._stdlib_contract import (
    LOAD_AGENT_PHASE,
    RUN_AGENT_PHASE,
    RUNTIME_PAYLOAD_FILENAME,
)
from ridges_harbor.runtime_contract import (
    MinerInvalidPatchError,
    MinerPatchApplyError,
    MinerRuntimeError,
    RidgesRuntimeFailure,
)

from .helpers import make_summary, successful_test_results, timing


def test_invalid_patch_exception_maps_to_agent_invalid_patch(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        exception_info={
            "exception_type": MinerInvalidPatchError.__name__,
            "exception_message": "Miner returned an invalid patch",
            "exception_traceback": "Traceback...\nridges_harbor/agents.py\n",
        },
        test_results=successful_test_results(),
    )

    with pytest.raises(EvaluationRunException) as exc_info:
        result_from_summary(summary)

    assert exc_info.value.error_code == EvaluationRunErrorCode.AGENT_INVALID_PATCH
    assert "invalid patch" in exc_info.value.error_message.lower()
    assert "miner logs" in exc_info.value.extra["agent_logs"]


def test_runtime_transport_failure_maps_to_validator_internal_error(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        exception_info={"exception_message": "transport failure"},
        runtime_failure={
            "phase": "runtime",
            "exception_chain": [
                {
                    "type": "ConnectionRefusedError",
                    "module": "requests.adapters",
                    "message": "connection refused",
                }
            ],
        },
    )

    with pytest.raises(EvaluationRunException) as exc_info:
        result_from_summary(summary)

    assert exc_info.value.error_code == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR
    assert "connection refused" in exc_info.value.error_message.lower()


def test_runtime_timeout_maps_to_validator_internal_error(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        exception_info={"exception_message": "request timed out"},
        runtime_failure={
            "phase": "runtime",
            "exception_chain": [
                {
                    "type": "TimeoutError",
                    "module": "httpx",
                    "message": "request timed out",
                }
            ],
        },
    )

    with pytest.raises(EvaluationRunException) as exc_info:
        result_from_summary(summary)

    assert exc_info.value.error_code == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR
    assert "timed out" in exc_info.value.error_message.lower()


def test_timeout_detection_wins_even_when_transport_signal_is_also_present() -> None:
    runtime_failure = RidgesRuntimeFailure.model_validate(
        {
            "phase": "runtime",
            "traceback": "Traceback...",
            "exception_chain": [
                {
                    "type": "TimeoutError",
                    "module": "httpx",
                    "message": "connection refused while request timed out",
                }
            ],
        }
    )

    assert looks_like_runtime_timeout(runtime_failure) is True
    assert looks_like_runtime_transport_error(runtime_failure) is True
    assert map_runtime_failure_code(runtime_failure) == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR


def test_invalid_runtime_payload_maps_to_validator_internal_error(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        exception_info={"exception_message": "runtime payload malformed"},
        runtime_payload={"phase": RUN_AGENT_PHASE},
    )

    with pytest.raises(EvaluationRunException) as exc_info:
        result_from_summary(summary)

    assert exc_info.value.error_code == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR
    assert "invalid ridges_runtime payload" in exc_info.value.error_message


def test_extract_runtime_failure_reads_runtime_file_when_agent_metadata_is_empty(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        runtime_failure={
            "phase": RUN_AGENT_PHASE,
            "missing_module": "requests",
        },
    )

    runtime_failure = extract_runtime_failure(summary)

    assert runtime_failure is not None
    assert runtime_failure.missing_module == "requests"
    assert summary.trial_result.agent_result is not None
    assert summary.trial_result.agent_result.metadata == {}


def test_extract_runtime_failure_returns_none_when_runtime_file_is_missing(tmp_path: Path) -> None:
    summary = make_summary(tmp_path)

    assert extract_runtime_failure(summary) is None


def test_extract_runtime_failure_rejects_invalid_json_file(tmp_path: Path) -> None:
    summary = make_summary(tmp_path)
    (summary.trial_dir / "agent" / RUNTIME_PAYLOAD_FILENAME).write_text("{")

    with pytest.raises(InvalidRuntimePayloadError):
        extract_runtime_failure(summary)


def test_patch_apply_exception_type_maps_to_agent_invalid_patch(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        exception_info={
            "exception_type": MinerPatchApplyError.__name__,
            "exception_message": "Failed to apply miner patch",
            "exception_traceback": "Traceback...\nridges_harbor/agents.py\n",
        },
    )

    with pytest.raises(EvaluationRunException) as exc_info:
        result_from_summary(summary)

    assert exc_info.value.error_code == EvaluationRunErrorCode.AGENT_INVALID_PATCH


def test_miner_runtime_exception_type_maps_to_agent_exception_running_agent(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        exception_info={
            "exception_type": MinerRuntimeError.__name__,
            "exception_message": "Legacy miner runtime failed",
            "exception_traceback": "Traceback...\nridges_harbor/agents.py\n",
        },
        agent_execution=timing(),
    )

    with pytest.raises(EvaluationRunException) as exc_info:
        result_from_summary(summary)

    assert exc_info.value.error_code == EvaluationRunErrorCode.AGENT_EXCEPTION_RUNNING_AGENT


def test_generic_agent_exception_maps_from_timing_only(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        exception_info={
            "exception_type": "RuntimeError",
            "exception_message": "agent crashed",
            "exception_traceback": "Traceback...\nno phase hints here\n",
        },
        agent_execution=timing(),
        test_results=successful_test_results(),
    )

    with pytest.raises(EvaluationRunException) as exc_info:
        result_from_summary(summary)

    assert exc_info.value.error_code == EvaluationRunErrorCode.AGENT_EXCEPTION_RUNNING_AGENT


def test_missing_baseline_module_maps_to_validator_internal_error(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        exception_info={"exception_message": "missing module"},
        runtime_failure={
            "phase": "runtime",
            "missing_module": "requests",
        },
    )

    with pytest.raises(EvaluationRunException) as exc_info:
        result_from_summary(summary)

    assert exc_info.value.error_code == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR
    assert "missing_module" in exc_info.value.error_message


def test_generic_runtime_failure_maps_to_agent_exception_running_agent(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        exception_info={"exception_message": "runtime failure"},
        runtime_failure={
            "phase": "runtime",
            "exception_chain": [
                {
                    "type": "ValueError",
                    "module": "agent",
                    "message": "boom",
                }
            ],
        },
    )

    with pytest.raises(EvaluationRunException) as exc_info:
        result_from_summary(summary)

    assert exc_info.value.error_code == EvaluationRunErrorCode.AGENT_EXCEPTION_RUNNING_AGENT


def test_http_503_runtime_failure_maps_to_validator_internal_error(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        exception_info={"exception_message": "provider returned 503"},
        runtime_failure={
            "phase": "runtime",
            "http_status": 503,
        },
    )

    with pytest.raises(EvaluationRunException) as exc_info:
        result_from_summary(summary)

    assert exc_info.value.error_code == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR


def test_http_400_runtime_failure_maps_to_agent_exception_running_agent(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        exception_info={"exception_message": "provider returned 400"},
        runtime_failure={
            "phase": "runtime",
            "http_status": 400,
        },
    )

    with pytest.raises(EvaluationRunException) as exc_info:
        result_from_summary(summary)

    assert exc_info.value.error_code == EvaluationRunErrorCode.AGENT_EXCEPTION_RUNNING_AGENT


def test_load_agent_runtime_failure_maps_to_agent_exception_running_agent(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        exception_info={
            "exception_type": "RuntimeError",
            "exception_message": "failed to load agent",
            "exception_traceback": "Traceback...",
        },
        runtime_failure={
            "phase": LOAD_AGENT_PHASE,
        },
    )

    with pytest.raises(EvaluationRunException) as exc_info:
        result_from_summary(summary)

    assert exc_info.value.error_code == EvaluationRunErrorCode.AGENT_EXCEPTION_RUNNING_AGENT


def test_verifier_egress_setup_failure_maps_to_validator_internal_error(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        exception_info={
            "exception_type": "VerifierEgressSetupError",
            "exception_message": "Failed to connect verifier egress",
            "exception_traceback": "Traceback...\nrunner.py\n_enable_verifier_egress\n",
        },
        agent_execution=timing(),
    )

    with pytest.raises(EvaluationRunException) as exc_info:
        result_from_summary(summary)

    assert exc_info.value.error_code == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR
    assert "Failed to connect verifier egress" in exc_info.value.error_message


def test_unsuccessful_verifier_returns_scored_execution_result(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        verifier_result={"rewards": {"reward": 0.0}},
    )

    result = result_from_summary(summary)

    assert result.verifier_reward == 0.0
    assert result.test_results == []


def test_agent_timeout_exception_maps_to_agent_timeout_running_agent(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        exception_info={
            "exception_type": "AgentTimeoutError",
            "exception_message": "Agent execution timed out after 30 seconds",
            "exception_traceback": "Traceback...\n_execute_agent\n",
        },
        agent_execution=timing(),
        test_results=successful_test_results(),
    )

    with pytest.raises(EvaluationRunException) as exc_info:
        result_from_summary(summary)

    assert exc_info.value.error_code == EvaluationRunErrorCode.AGENT_TIMEOUT_RUNNING_AGENT


def test_agent_setup_timeout_exception_maps_to_validator_internal_error(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        exception_info={
            "exception_type": "AgentSetupTimeoutError",
            "exception_message": "Agent setup timed out after 360 seconds",
            "exception_traceback": "Traceback...\n_setup_agent\n",
        },
        agent_setup=timing(),
        test_results=successful_test_results(),
    )

    with pytest.raises(EvaluationRunException) as exc_info:
        result_from_summary(summary)

    assert exc_info.value.error_code == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR


def test_verifier_timeout_exception_maps_to_agent_timeout_running_eval(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        exception_info={
            "exception_type": "VerifierTimeoutError",
            "exception_message": "Verifier execution timed out after 60 seconds",
            "exception_traceback": "Traceback...\n_verify_with_retry\n",
        },
        agent_execution=timing(),
        verifier=timing(),
        test_results=successful_test_results(),
    )

    with pytest.raises(EvaluationRunException) as exc_info:
        result_from_summary(summary)

    assert exc_info.value.error_code == EvaluationRunErrorCode.AGENT_TIMEOUT_RUNNING_EVAL


def test_generic_verifier_exception_maps_to_validator_internal_error_from_timing_only(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        exception_info={
            "exception_type": "RuntimeError",
            "exception_message": "verifier command exited non-zero",
            "exception_traceback": "Traceback...\nno phase hints here\n",
        },
        agent_execution=timing(),
        verifier=timing(),
        test_results=successful_test_results(),
    )

    with pytest.raises(EvaluationRunException) as exc_info:
        result_from_summary(summary)

    assert exc_info.value.error_code == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR


def test_generic_environment_exception_maps_from_timing_only(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        exception_info={
            "exception_type": "RuntimeError",
            "exception_message": "environment setup failed",
            "exception_traceback": "Traceback...\nno phase hints here\n",
        },
        environment_setup=timing(),
        test_results=successful_test_results(),
    )

    with pytest.raises(EvaluationRunException) as exc_info:
        result_from_summary(summary)

    assert exc_info.value.error_code == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR


def test_environment_timeout_exception_maps_to_validator_internal_error(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        exception_info={
            "exception_type": "EnvironmentStartTimeoutError",
            "exception_message": "Environment start timed out after 30 seconds",
            "exception_traceback": "Traceback...\n_start_environment_with_retry\n",
        },
        environment_setup=timing(),
        test_results=successful_test_results(),
    )

    with pytest.raises(EvaluationRunException) as exc_info:
        result_from_summary(summary)

    assert exc_info.value.error_code == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR


def test_generic_timeout_without_phase_maps_to_validator_internal_error(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        exception_info={
            "exception_type": "TimeoutError",
            "exception_message": "operation timed out",
            "exception_traceback": "Traceback...",
        },
        test_results=successful_test_results(),
    )

    with pytest.raises(EvaluationRunException) as exc_info:
        result_from_summary(summary)

    assert exc_info.value.error_code == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR


def test_unknown_exception_without_timing_maps_to_validator_internal_error(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        exception_info={
            "exception_type": "UnexpectedPhaseError",
            "exception_message": "boom",
            "exception_traceback": "Traceback...\nno phase hints here\n",
        },
        test_results=successful_test_results(),
    )

    with pytest.raises(EvaluationRunException) as exc_info:
        result_from_summary(summary)

    assert exc_info.value.error_code == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR


def test_cleanup_failure_after_successful_verifier_maps_to_validator_internal_error(tmp_path: Path) -> None:
    summary = make_summary(
        tmp_path,
        exception_info={
            "exception_type": "RuntimeError",
            "exception_message": "failed to stop environment",
            "exception_traceback": "Traceback...",
        },
        agent_execution=timing(),
        verifier=timing(),
        verifier_result={"rewards": {"reward": 1.0}},
        test_results=successful_test_results(),
    )

    with pytest.raises(EvaluationRunException) as exc_info:
        result_from_summary(summary)

    assert exc_info.value.error_code == EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR
