"""
Classify Harbor trial failures into platform error codes.

Two signals feed the decision: Harbor's 'ExceptionInfo' on the trial result
and the optional 'ridges_runtime.json' failure payload the agent writes.

Known exception types route directly into agent vs validator buckets;
anything else is placed by inferring which phase (setup, agent, environment, verifier)
the failure landed in from Harbor's timing info.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from harbor.trial.trial import (
    AgentSetupTimeoutError,
    AgentTimeoutError,
    EnvironmentStartTimeoutError,
    VerifierTimeoutError,
)
from harbor.verifier.verifier import (
    AddTestsDirError,
    DownloadVerifierDirError,
    RewardFileEmptyError,
    RewardFileNotFoundError,
    VerifierOutputParseError,
)
from pydantic import ValidationError

from execution.types import ClassifiedExecutionFailure
from models.evaluation_run import EvaluationRunErrorCode
from ridges_harbor._stdlib_contract import (
    LOAD_AGENT_PHASE,
    RUN_AGENT_PHASE,
    RUNTIME_PAYLOAD_FILENAME,
)
from ridges_harbor.docker_runtime import VerifierEgressSetupError
from ridges_harbor.runner import HarborRunSummary
from ridges_harbor.runtime_contract import (
    MinerInvalidPatchError,
    MinerPatchApplyError,
    MinerRuntimeError,
    RidgesRuntimeFailure,
)

if TYPE_CHECKING:
    from harbor.models.trial.result import ExceptionInfo, TimingInfo, TrialResult

TIMEOUT_EXCEPTION_NAMES = {
    "ConnectTimeout",
    "ConnectTimeoutError",
    "ReadTimeout",
    "ReadTimeoutError",
    "Timeout",
    "TimeoutError",
}

TRANSPORT_EXCEPTION_NAMES = {
    "ConnectError",
    "ConnectionError",
    "ConnectionRefusedError",
    "ConnectTimeout",
    "ConnectTimeoutError",
    "NewConnectionError",
    "ProxyError",
    "ReadTimeout",
    "ReadTimeoutError",
    "SSLError",
    "Timeout",
    "gaierror",
}

TRANSPORT_MESSAGE_PATTERNS = (
    "connection refused",
    "failed to establish a new connection",
    "name or service not known",
    "nodename nor servname provided",
    "temporary failure in name resolution",
)

BASELINE_RUNTIME_MODULES = (
    "httpx",
    "pydantic",
    "requests",
    "tree_sitter",
    "tree_sitter_language_pack",
)

AGENT_TIMEOUT_EXCEPTION_NAMES = {AgentTimeoutError.__name__}

VERIFIER_TIMEOUT_EXCEPTION_NAMES = {VerifierTimeoutError.__name__}

VERIFIER_INTERNAL_EXCEPTION_NAMES = {
    AddTestsDirError.__name__,
    DownloadVerifierDirError.__name__,
    RewardFileEmptyError.__name__,
    RewardFileNotFoundError.__name__,
    VerifierEgressSetupError.__name__,
    VerifierOutputParseError.__name__,
}

ENVIRONMENT_INTERNAL_EXCEPTION_NAMES = {
    AgentSetupTimeoutError.__name__,
    EnvironmentStartTimeoutError.__name__,
}

MINER_RUNTIME_EXCEPTION_NAMES = {MinerRuntimeError.__name__}

MINER_INVALID_PATCH_EXCEPTION_NAMES = {
    MinerInvalidPatchError.__name__,
    MinerPatchApplyError.__name__,
}


class InvalidRuntimePayloadError(RuntimeError):
    """Raised when ridges_runtime.json is present but does not match the contract."""


def extract_runtime_failure(summary: HarborRunSummary) -> RidgesRuntimeFailure | None:
    """Load and validate 'trial_dir/agent/ridges_runtime.json', or None when missing.

    A present-but-malformed file raises InvalidRuntimePayloadError.
    """
    runtime_payload_path = summary.trial_dir / "agent" / RUNTIME_PAYLOAD_FILENAME
    if not runtime_payload_path.exists():
        return None

    try:
        runtime_payload = json.loads(runtime_payload_path.read_text())
        return RidgesRuntimeFailure.model_validate(runtime_payload)
    except (OSError, json.JSONDecodeError, ValidationError) as exception:
        raise InvalidRuntimePayloadError(str(exception)) from exception


def classify_trial_failure(
    trial_result: TrialResult,
    trial_exception: ExceptionInfo,
    runtime_failure: RidgesRuntimeFailure | None,
) -> ClassifiedExecutionFailure:
    """Classify a failed Harbor trial into one platform error code.

    A structured 'runtime_failure' (written by ridges_miner_runtime) takes
    precedence over Harbor's 'trial_exception' when present.
    """
    if runtime_failure is not None:
        return ClassifiedExecutionFailure(
            error_code=map_runtime_failure_code(runtime_failure=runtime_failure),
            detail=runtime_failure.model_dump_json(indent=2),
        )

    return ClassifiedExecutionFailure(
        error_code=map_trial_exception_code(trial_result=trial_result, trial_exception=trial_exception),
        detail=trial_exception.model_dump_json(indent=2),
    )


def map_trial_exception_code(
    trial_result: TrialResult,
    trial_exception: ExceptionInfo,
) -> EvaluationRunErrorCode:
    """Map Harbor exceptions into agent vs validator error codes."""
    exception_type = trial_exception.exception_type.strip()

    if exception_type in MINER_INVALID_PATCH_EXCEPTION_NAMES:
        return EvaluationRunErrorCode.AGENT_INVALID_PATCH

    if exception_type in ENVIRONMENT_INTERNAL_EXCEPTION_NAMES:
        return EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR

    if exception_type in AGENT_TIMEOUT_EXCEPTION_NAMES:
        return EvaluationRunErrorCode.AGENT_TIMEOUT_RUNNING_AGENT

    if exception_type in VERIFIER_TIMEOUT_EXCEPTION_NAMES:
        return EvaluationRunErrorCode.AGENT_TIMEOUT_RUNNING_EVAL

    if exception_type in VERIFIER_INTERNAL_EXCEPTION_NAMES:
        return EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR

    inferred_phase = infer_trial_exception_phase(trial_result=trial_result, trial_exception=trial_exception)
    if inferred_phase == RUN_AGENT_PHASE:
        return EvaluationRunErrorCode.AGENT_EXCEPTION_RUNNING_AGENT

    return EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR


def infer_trial_exception_phase(
    trial_result: TrialResult,
    trial_exception: ExceptionInfo,
) -> str | None:
    """Infer which Harbor phase most likely raised the exception.

    Walks backward through Harbor's phase timing (verifier -> agent_execution
    -> agent_setup -> environment_setup) to find the last phase that had any
    activity. Falls back to exception type when timing is empty. Returns one
    of 'post_verifier', 'verify', 'run_agent', 'environment', or None.
    """
    exception_type = trial_exception.exception_type.strip()

    if trial_result.verifier_result is not None:
        return "post_verifier"

    if _timing_present(value=trial_result.verifier):
        return "verify"

    if _timing_present(value=trial_result.agent_execution):
        return RUN_AGENT_PHASE

    if _timing_present(value=trial_result.agent_setup) or _timing_present(value=trial_result.environment_setup):
        return "environment"

    if exception_type in VERIFIER_TIMEOUT_EXCEPTION_NAMES:
        return "verify"

    if exception_type in AGENT_TIMEOUT_EXCEPTION_NAMES:
        return RUN_AGENT_PHASE

    if exception_type in MINER_RUNTIME_EXCEPTION_NAMES | MINER_INVALID_PATCH_EXCEPTION_NAMES:
        return RUN_AGENT_PHASE

    if exception_type in ENVIRONMENT_INTERNAL_EXCEPTION_NAMES:
        return "environment"

    return None


def _timing_present(value: TimingInfo | None) -> bool:
    """Return True when a Harbor TimingInfo slot has started or finished."""
    return value is not None and (value.started_at is not None or value.finished_at is not None)


def map_runtime_failure_code(runtime_failure: RidgesRuntimeFailure) -> EvaluationRunErrorCode:
    """Map a structured runtime failure into a platform error code.

    Returns VALIDATOR_INTERNAL_ERROR for infra issues (missing baseline module,
    HTTP 5xx, transport timeouts, transport errors). Everything else is
    treated as an agent-side crash (AGENT_EXCEPTION_RUNNING_AGENT).
    """
    phase = runtime_failure.phase.strip().lower()
    http_status = runtime_failure.http_status
    missing_module = runtime_failure.missing_module

    if isinstance(missing_module, str) and missing_module in BASELINE_RUNTIME_MODULES:
        return EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR

    if phase == LOAD_AGENT_PHASE:
        return EvaluationRunErrorCode.AGENT_EXCEPTION_RUNNING_AGENT

    if isinstance(http_status, int):
        if http_status >= 500:
            return EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR
        return EvaluationRunErrorCode.AGENT_EXCEPTION_RUNNING_AGENT

    if looks_like_runtime_timeout(runtime_failure=runtime_failure):
        return EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR

    if looks_like_runtime_transport_error(runtime_failure=runtime_failure):
        return EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR

    return EvaluationRunErrorCode.AGENT_EXCEPTION_RUNNING_AGENT


def looks_like_runtime_timeout(runtime_failure: RidgesRuntimeFailure) -> bool:
    """Detect whether the runtime payload looks like a transport-layer timeout.

    Matches on both the exception type name and its module, so an
    application-level 'TimeoutError' from business code doesn't false-positive.
    """
    for item in runtime_failure.exception_chain:
        exception_type = item.type.strip()
        exception_module = item.module.lower()

        if exception_type in TIMEOUT_EXCEPTION_NAMES and any(
            part in exception_module for part in ("requests", "httpx", "urllib3", "socket")
        ):
            return True

    return False


def looks_like_runtime_transport_error(runtime_failure: RidgesRuntimeFailure) -> bool:
    """Detect whether the runtime payload looks like a transport-layer error.

    Checks exception types against a known set plus a message-pattern
    allowlist for name-resolution and connection-refused errors whose type
    name varies by platform.
    """
    for item in runtime_failure.exception_chain:
        exception_type = item.type.strip()
        if exception_type in TRANSPORT_EXCEPTION_NAMES:
            return True

        lowered_message = item.message.lower()
        if any(pattern in lowered_message for pattern in TRANSPORT_MESSAGE_PATTERNS):
            return True
    return False
