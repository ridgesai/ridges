import pytest

from models.evaluation_run import EvaluationRunErrorCode, is_retryable_error_code


@pytest.mark.parametrize(
    "code",
    [
        EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR,  # 2000
        EvaluationRunErrorCode.VALIDATOR_FAILED_PENDING,  # 2010
        EvaluationRunErrorCode.VALIDATOR_FAILED_INIT_AGENT,  # 2020
        EvaluationRunErrorCode.VALIDATOR_FAILED_RUNNING_AGENT,  # 2030
        EvaluationRunErrorCode.VALIDATOR_FAILED_INIT_EVAL,  # 2040
        EvaluationRunErrorCode.VALIDATOR_FAILED_RUNNING_EVAL,  # 2050
        EvaluationRunErrorCode.PLATFORM_FAILED_PROVISIONING,  # 3050
    ],
)
def test_retryable_codes(code):
    assert is_retryable_error_code(int(code)) is True


@pytest.mark.parametrize(
    "code",
    [
        int(EvaluationRunErrorCode.AGENT_EXCEPTION_RUNNING_AGENT),  # 1000: agent fault
        int(EvaluationRunErrorCode.AGENT_INVALID_PATCH),  # 1040: agent fault
        int(EvaluationRunErrorCode.VALIDATOR_UNKNOWN_PROBLEM),  # 2060: deterministic
        int(EvaluationRunErrorCode.PLATFORM_RESTARTED_WHILE_PENDING),  # 3000: session dead
        int(EvaluationRunErrorCode.PLATFORM_RESTARTED_WHILE_RUNNING_EVAL),  # 3040: session dead
        int(EvaluationRunErrorCode.PLATFORM_PRUNED_BY_SCORE_BOUND),  # 3060: pointless
        1234,  # unknown 1xxx-range code
        9999,  # out of range
    ],
)
def test_non_retryable_codes(code):
    assert is_retryable_error_code(code) is False


def test_none_is_not_retryable():
    assert is_retryable_error_code(None) is False
