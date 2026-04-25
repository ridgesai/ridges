"""Dataclasses passed between the execution-layer modules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from execution.errors import EvaluationRunException
from models.evaluation_run import EvaluationRunErrorCode
from models.problem import ProblemTestResult


@dataclass(slots=True)
class ExecutionResult:
    """The patch, test results, and logs from a successful Harbor run."""

    backend: str
    patch: str
    verifier_reward: float
    test_results: list[ProblemTestResult]
    agent_logs: str
    eval_logs: str
    job_dir: Path | None = None


@dataclass(slots=True, frozen=True)
class TrialSnapshot:
    """The completed agent-phase output at Harbor verifier start."""

    patch: str
    agent_logs: str


@dataclass(slots=True)
class ClassifiedExecutionFailure:
    """A Harbor failure after it has been mapped to a platform error code."""

    error_code: EvaluationRunErrorCode
    detail: str


@dataclass(slots=True, frozen=True)
class FailureContext:
    """Collected logs and job dir attached to execution-layer failures."""

    agent_logs: str
    eval_logs: str
    job_dir: Path | None = None

    def as_extra(self) -> dict[str, Any]:
        """Render the context as an 'EvaluationRunException.extra' dict, omitting empties."""
        extra: dict[str, Any] = {}
        if self.agent_logs:
            extra["agent_logs"] = self.agent_logs
        if self.eval_logs:
            extra["eval_logs"] = self.eval_logs
        if self.job_dir is not None:
            extra["job_dir"] = self.job_dir
        return extra

    def fail(
        self,
        error_code: EvaluationRunErrorCode,
        error_message: str,
        *,
        cause: BaseException | None = None,
    ) -> NoReturn:
        """Raise an EvaluationRunException, chaining 'cause' when provided."""
        exception = EvaluationRunException(
            error_code=error_code,
            error_message=error_message,
            extra=self.as_extra(),
        )
        if cause is not None:
            raise exception from cause
        raise exception

    def fail_validator(
        self,
        error_message: str,
        *,
        cause: BaseException | None = None,
    ) -> NoReturn:
        """Raise VALIDATOR_INTERNAL_ERROR with the context attached."""
        self.fail(
            error_code=EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR,
            error_message=error_message,
            cause=cause,
        )

    def fail_agent_eval(
        self,
        error_message: str,
        *,
        cause: BaseException | None = None,
    ) -> NoReturn:
        """Raise AGENT_EXCEPTION_RUNNING_EVAL with the context attached."""
        self.fail(
            error_code=EvaluationRunErrorCode.AGENT_EXCEPTION_RUNNING_EVAL,
            error_message=error_message,
            cause=cause,
        )


@dataclass(slots=True, frozen=True)
class ExecutionRunRequest:
    """Resolved inputs for one remote Harbor execution."""

    task_dir: Path
    task_name: str
    task_digest: str
    agent_timeout_sec: float | None
    results_dir: Path
    job_name: str

    @property
    def job_dir(self) -> Path:
        """The per-run job directory ('results_dir / job_name') Harbor writes into."""
        return self.results_dir / self.job_name
