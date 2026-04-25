"""Drive one Harbor evaluation run end-to-end."""

from __future__ import annotations

import tempfile
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import ValidationError

import utils.logger as logger
from execution.artifacts import collect_job_crash_context, read_trial_snapshot, result_from_summary
from execution.errors import EvaluationRunException
from execution.types import ExecutionResult, ExecutionRunRequest, TrialSnapshot
from models.evaluation_run import EvaluationRunErrorCode
from models.harbor_task import HarborRemoteTaskExecutionSpec
from ridges_harbor.runner import DEFAULT_RESULTS_DIR, run_task
from utils.task_cache import get_cached_task, get_or_download_task

_JOB_NAME_FORMAT = "{problem_name}__{evaluation_run_id}"


def _format_job_name(problem_name: str, evaluation_run_id: UUID) -> str:
    """Format the job name Harbor uses for one evaluation run."""
    return _JOB_NAME_FORMAT.format(
        problem_name=problem_name,
        evaluation_run_id=evaluation_run_id,
    )


@contextmanager
def resolved_agent_source(
    *,
    agent_path: str | Path | None,
    agent_code: str | None,
) -> Iterator[Path]:
    """Yield a path to the agent source, writing a temp file when only 'agent_code' is given.

    Exactly one of 'agent_path' or 'agent_code' must be set. The temp file, if
    created, is deleted on exit.
    """

    temp_agent_path: Path | None = None
    try:
        if agent_path is not None:
            yield Path(agent_path).expanduser().resolve()
            return

        if agent_code is None:
            raise EvaluationRunException(
                EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR,
                "Harbor execution requires either an agent_path or agent_code",
            )

        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as handle:
            handle.write(agent_code)
            temp_agent_path = Path(handle.name)
        yield temp_agent_path

    finally:
        if temp_agent_path is not None and temp_agent_path.exists():
            temp_agent_path.unlink()


class ExecutionEngine:
    """Run one promoted Harbor task for one evaluation run."""

    def __init__(
        self,
        inference_url: str,
        *,
        harbor_results_dir: str | Path | None = None,
        harbor_debug: bool = False,
    ):
        self.inference_url = inference_url
        self.results_dir = harbor_results_dir
        self.debug = harbor_debug

    async def evaluate(
        self,
        *,
        evaluation_run_id: UUID,
        problem_name: str,
        execution_spec: dict[str, Any] | None,
        agent_path: str | Path | None,
        agent_code: str | None,
        fetch_task_url: Callable[[str], Awaitable[str]] | None = None,
        on_agent_started: Callable[[], Awaitable[None]] | None = None,
        on_verification_started: Callable[[TrialSnapshot], Awaitable[None]] | None = None,
    ) -> ExecutionResult:
        """Run the task referenced by the evaluation set and normalize the result.

        Resolves the cached task, hands control to the Harbor runner, and turns
        the outcome into either a success result or a classified failure.
        Unexpected engine-level crashes are wrapped as VALIDATOR_INTERNAL_ERROR.
        """

        parsed_spec = self._parse_execution_spec(problem_name=problem_name, execution_spec=execution_spec)
        task_dir = await self._resolve_task_dir(
            execution_spec=parsed_spec, problem_name=problem_name, fetch_task_url=fetch_task_url
        )
        request = self._build_run_request(
            evaluation_run_id=evaluation_run_id,
            parsed_spec=parsed_spec,
            task_dir=task_dir,
            problem_name=problem_name,
        )

        try:

            async def harbor_on_agent_started(_event: Any) -> None:
                try:
                    await on_agent_started()
                except Exception as exception:
                    logger.warning(f"Harbor on_agent_started callback failed: {exception}")

            async def harbor_on_verification_started(event: Any) -> None:
                try:
                    trial_dir = Path(event.config.trials_dir) / event.trial_id
                    await on_verification_started(read_trial_snapshot(trial_dir))
                except Exception as exception:
                    logger.warning(f"Harbor on_verification_started callback failed: {exception}")

            with resolved_agent_source(agent_path=agent_path, agent_code=agent_code) as resolved_agent_path:
                summary = await run_task(
                    request.task_dir,
                    task_name=request.task_name,
                    task_digest=request.task_digest,
                    evaluation_run_id=str(evaluation_run_id),
                    agent_path=resolved_agent_path,
                    agent_timeout_sec=request.agent_timeout_sec,
                    inference_url=self.inference_url,
                    results_dir=request.results_dir,
                    debug=self.debug,
                    job_name=request.job_name,
                    on_agent_started=harbor_on_agent_started if on_agent_started is not None else None,
                    on_verification_started=(
                        harbor_on_verification_started if on_verification_started is not None else None
                    ),
                )
            return result_from_summary(summary=summary)

        except EvaluationRunException:
            raise

        except Exception as exception:
            base_message = EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR.get_error_message()

            raise EvaluationRunException(
                error_code=EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR,
                error_message=f"{base_message}: {exception}",
                extra=collect_job_crash_context(job_dir=request.job_dir),
            ) from exception

    def _parse_execution_spec(
        self,
        problem_name: str,
        execution_spec: dict[str, Any] | None,
    ) -> HarborRemoteTaskExecutionSpec:
        """Validate the execution spec into a typed Harbor remote task spec."""
        if execution_spec is None:
            raise EvaluationRunException(
                error_code=EvaluationRunErrorCode.VALIDATOR_UNKNOWN_PROBLEM,
                error_message=f"The active evaluation set item '{problem_name}' does not define an execution spec",
            )

        kind = execution_spec.get("kind")
        if kind != "harbor_remote_task":
            raise EvaluationRunException(
                error_code=EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR,
                error_message=f"Only promoted remote Harbor tasks are supported for '{problem_name}'; got kind={kind!r}",
            )

        try:
            return HarborRemoteTaskExecutionSpec.model_validate(execution_spec)
        except ValidationError as exception:
            raise EvaluationRunException(
                error_code=EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR,
                error_message=f"Invalid Harbor execution spec for '{problem_name}': {exception}",
            ) from exception

    def _build_run_request(
        self,
        *,
        evaluation_run_id: UUID,
        parsed_spec: HarborRemoteTaskExecutionSpec,
        task_dir: Path,
        problem_name: str,
    ) -> ExecutionRunRequest:
        """Bundle the resolved inputs needed to invoke the Harbor runner."""
        job_name = _format_job_name(problem_name=problem_name, evaluation_run_id=evaluation_run_id)
        results_dir = Path(self.results_dir or DEFAULT_RESULTS_DIR).expanduser().resolve()

        return ExecutionRunRequest(
            task_dir=task_dir,
            task_name=parsed_spec.task_name,
            task_digest=parsed_spec.task_digest,
            agent_timeout_sec=parsed_spec.agent_timeout_sec,
            results_dir=results_dir,
            job_name=job_name,
        )

    async def _resolve_task_dir(
        self,
        execution_spec: HarborRemoteTaskExecutionSpec,
        problem_name: str,
        fetch_task_url: Callable[[str], Awaitable[str]] | None,
    ) -> Path:
        """Resolve the task directory from the local cache, downloading if needed."""
        cached = get_cached_task(task_name=execution_spec.task_name, task_digest=execution_spec.task_digest)
        if cached:
            return cached

        if not fetch_task_url:
            raise EvaluationRunException(
                error_code=EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR,
                error_message=f"No URL fetcher for remote task '{problem_name}' (digest: {execution_spec.task_digest})",
            )

        url = await fetch_task_url(execution_spec.task_digest)
        return await get_or_download_task(
            presigned_url=url, task_name=execution_spec.task_name, task_digest=execution_spec.task_digest
        )
