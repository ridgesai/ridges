"""Models and enums for a single evaluation run."""

from datetime import datetime
from enum import Enum, IntEnum
from typing import Any, List, Optional
from uuid import UUID

from pydantic import BaseModel

from models.problem import ProblemTestResult


class EvaluationRunErrorCode(IntEnum):
    """Platform error codes for evaluation runs.

    The 1xxx, 2xxx, and 3xxx ranges are used by the platform
    to decide whether a run counts as agent fault, validator fault, or platform
    fault.
    """

    # ADAM: Magic
    def __new__(cls, code: int, message: str):
        obj = int.__new__(cls, code)
        obj._value_ = code
        obj.message = message
        return obj

    # 1xxx - Agent Errors
    AGENT_EXCEPTION_RUNNING_AGENT = (1000, "The agent raised an exception while being run")
    AGENT_EXCEPTION_RUNNING_EVAL = (1010, "The agent raised an exception while being evaluated")
    AGENT_TIMEOUT_RUNNING_AGENT = (1020, "The agent timed out while being run")
    AGENT_TIMEOUT_RUNNING_EVAL = (1030, "The agent timed out while being evaluated")
    AGENT_INVALID_PATCH = (1040, "The agent returned an invalid patch")
    AGENT_KEY_UNREADABLE = (1050, "The agent's uploaded OpenRouter API key could not be decrypted")

    # 2xxx - Validator Errors
    VALIDATOR_INTERNAL_ERROR = (2000, "An internal error occurred on the validator")
    VALIDATOR_FAILED_PENDING = (
        2010,
        "An internal error occurred on the validator while the evaluation run was pending",
    )
    VALIDATOR_FAILED_INIT_AGENT = (
        2020,
        "An internal error occurred on the validator while the evaluation run was initializing the agent",
    )
    VALIDATOR_FAILED_RUNNING_AGENT = (
        2030,
        "An internal error occurred on the validator while the evaluation run was running the agent",
    )
    VALIDATOR_FAILED_INIT_EVAL = (
        2040,
        "An internal error occurred on the validator while the evaluation run was initializing the evaluation",
    )
    VALIDATOR_FAILED_RUNNING_EVAL = (
        2050,
        "An internal error occurred on the validator while the evaluation run was running the evaluation",
    )
    VALIDATOR_UNKNOWN_PROBLEM = (2060, "Unknown problem")

    # 3xxx - Platform Errors
    PLATFORM_RESTARTED_WHILE_PENDING = (3000, "The platform was restarted while the evaluation run was pending")
    PLATFORM_RESTARTED_WHILE_INIT_AGENT = (
        3010,
        "The platform was restarted while the evaluation run was initializing the agent",
    )
    PLATFORM_RESTARTED_WHILE_RUNNING_AGENT = (
        3020,
        "The platform was restarted while the evaluation run was running the agent",
    )
    PLATFORM_RESTARTED_WHILE_INIT_EVAL = (
        3030,
        "The platform was restarted while the evaluation run was initializing the evaluation",
    )
    PLATFORM_RESTARTED_WHILE_RUNNING_EVAL = (
        3040,
        "The platform was restarted while the evaluation run was running the evaluation",
    )
    PLATFORM_FAILED_PROVISIONING = (3050, "Platform failed to provision task resources")
    PLATFORM_PRUNED_BY_SCORE_BOUND = (
        3060,
        "The platform stopped this run because the agent could no longer meet the required score bound",
    )

    def get_error_message(self) -> str:
        return self.message

    def is_agent_error(self) -> bool:
        return 1000 <= self.value < 2000

    def is_validator_error(self) -> bool:
        return 2000 <= self.value < 3000

    def is_platform_error(self) -> bool:
        return 3000 <= self.value < 4000


# Non-agent errors that must never trigger an in-session retry: score-bound
# pruning is terminal by definition, PLATFORM_RESTARTED_* are only set by bulk
# cleanup after the owning session is already gone, and an unknown problem
# fails identically on every attempt.
NON_RETRYABLE_PLATFORM_ERROR_CODES = frozenset(
    {
        EvaluationRunErrorCode.VALIDATOR_UNKNOWN_PROBLEM.value,
        EvaluationRunErrorCode.PLATFORM_RESTARTED_WHILE_PENDING.value,
        EvaluationRunErrorCode.PLATFORM_RESTARTED_WHILE_INIT_AGENT.value,
        EvaluationRunErrorCode.PLATFORM_RESTARTED_WHILE_RUNNING_AGENT.value,
        EvaluationRunErrorCode.PLATFORM_RESTARTED_WHILE_INIT_EVAL.value,
        EvaluationRunErrorCode.PLATFORM_RESTARTED_WHILE_RUNNING_EVAL.value,
        EvaluationRunErrorCode.PLATFORM_PRUNED_BY_SCORE_BOUND.value,
    }
)


def is_retryable_error_code(error_code: int | None) -> bool:
    """Whether an errored run may be retried in-session with a fresh attempt.

    Agent errors (1xxx) already count toward evaluation success and are never
    retried; only validator/platform faults outside the denylist qualify.
    """
    if error_code is None:
        return False
    code = int(error_code)
    return 2000 <= code < 4000 and code not in NON_RETRYABLE_PLATFORM_ERROR_CODES


class EvaluationRunStatus(str, Enum):
    """Lifecycle states for one problem inside an evaluation."""

    pending = "pending"
    initializing_agent = "initializing_agent"
    running_agent = "running_agent"
    initializing_eval = "initializing_eval"
    running_eval = "running_eval"
    finished = "finished"
    error = "error"


class EvaluationRunAttempt(BaseModel):
    """One attempt at executing an evaluation run. The evaluation_runs row mirrors the latest attempt."""

    attempt_id: UUID
    evaluation_run_id: UUID
    attempt_number: int
    status: EvaluationRunStatus

    error_code: Optional[int] = None
    error_message: Optional[str] = None
    cost_usd: Optional[float] = None

    created_at: datetime
    started_initializing_agent_at: Optional[datetime] = None
    started_running_agent_at: Optional[datetime] = None
    started_initializing_eval_at: Optional[datetime] = None
    started_running_eval_at: Optional[datetime] = None
    finished_or_errored_at: Optional[datetime] = None


class EvaluationRun(BaseModel):
    """Persisted state for one problem run inside a broader evaluation."""

    evaluation_run_id: UUID
    evaluation_id: UUID
    problem_name: str
    problem_alias: str | None = None
    benchmark_family: str | None = None
    execution_spec: dict[str, Any] | None = None

    status: EvaluationRunStatus

    patch: Optional[str] = None
    test_results: Optional[List[ProblemTestResult]] = None
    verifier_reward: Optional[float] = None

    error_code: Optional[EvaluationRunErrorCode] = None
    error_message: Optional[str] = None
    cost_usd: Optional[float] = None

    created_at: datetime
    started_initializing_agent_at: Optional[datetime] = None
    started_running_agent_at: Optional[datetime] = None
    started_initializing_eval_at: Optional[datetime] = None
    started_running_eval_at: Optional[datetime] = None
    finished_or_errored_at: Optional[datetime] = None


class EvaluationRunDetail(EvaluationRun):
    """EvaluationRun enriched with peer-comparison metrics."""

    run_time_seconds: float | None = None
    problem_total_runs: int | None = None
    problem_average_time_seconds: float | None = None
    problem_average_cost_usd: float | None = None


class EvaluationRunLogType(str, Enum):
    """The two log streams stored for an evaluation run."""

    agent = "agent"
    eval = "eval"
