"""Turn a promoted Harbor execution spec into a one-task Harbor job."""

from __future__ import annotations

import asyncio
import traceback
from pathlib import Path
from typing import Any
from uuid import uuid4

from ridges_harbor._stdlib_contract import HARBOR_RUNNER_ERROR_FILENAME
from ridges_harbor.digest import compute_task_digest
from ridges_harbor.docker_runtime import (
    TrialHook,
    build_enable_verifier_egress_hook,
    docker_environment_env,
)
from ridges_harbor.shared import DEFAULT_RESULTS_DIR, HarborRunSummary, resolve_inference_gateway

DEFAULT_AGENT_SANDBOX_PROXY_URL = "http://sandbox-proxy:80"


def _write_runner_exception(job_dir: Path) -> Path:
    """Write the current traceback next to the Harbor job output."""
    job_dir.mkdir(parents=True, exist_ok=True)
    error_path = job_dir / HARBOR_RUNNER_ERROR_FILENAME
    error_path.write_text(traceback.format_exc())
    return error_path


def _harbor_agent_env(
    *,
    evaluation_run_id: str,
    agent_timeout_sec: float | None,
) -> dict[str, str]:
    """Build the env dict Harbor merges into every agent command."""
    normalized_timeout: str | None = None
    if agent_timeout_sec is not None:
        timeout = float(str(agent_timeout_sec).strip())
        if timeout > 0:
            normalized_timeout = str(int(timeout)) if timeout.is_integer() else str(timeout)

    env = {
        "EVALUATION_RUN_ID": evaluation_run_id,
        "SANDBOX_PROXY_URL": DEFAULT_AGENT_SANDBOX_PROXY_URL,
    }
    if normalized_timeout is not None:
        env["AGENT_TIMEOUT"] = normalized_timeout
    return env


async def run_task(
    task_dir: str | Path,
    *,
    task_name: str,
    task_digest: str,
    evaluation_run_id: str,
    agent_path: str | Path,
    agent_timeout_sec: float | None = None,
    inference_url: str | None = None,
    results_dir: str | Path | None = DEFAULT_RESULTS_DIR,
    debug: bool = False,
    job_name: str | None = None,
    on_agent_started: TrialHook | None = None,
    on_verification_started: TrialHook | None = None,
) -> HarborRunSummary:
    """Run a pre-built Harbor task directory after verifying its digest.

    The caller is responsible for obtaining the task directory — either from the
    local filesystem or from the remote task cache. This function only verifies
    the content digest and hands the directory to Harbor.
    """
    resolved_task_dir = Path(task_dir).expanduser().resolve()
    resolved_agent_path = Path(agent_path).expanduser().resolve()
    resolved_results_dir = Path(results_dir or DEFAULT_RESULTS_DIR).expanduser().resolve()
    resolved_results_dir.mkdir(parents=True, exist_ok=True)
    upstream_url, upstream_host = resolve_inference_gateway(inference_url)

    if not resolved_task_dir.exists():
        raise FileNotFoundError(f"Harbor task directory does not exist: {resolved_task_dir}")

    actual_digest = await asyncio.to_thread(compute_task_digest, resolved_task_dir)
    if actual_digest != task_digest:
        raise RuntimeError(f"Harbor task digest mismatch for {task_name}: expected {task_digest}, got {actual_digest}")

    summary = await _run_task_dir(
        task_dir=resolved_task_dir,
        task_name=task_name,
        evaluation_run_id=evaluation_run_id,
        agent_path=resolved_agent_path,
        agent_timeout_sec=agent_timeout_sec,
        upstream_url=upstream_url,
        upstream_host=upstream_host,
        results_dir=resolved_results_dir,
        debug=debug,
        job_name=job_name,
        on_agent_started=on_agent_started,
        on_verification_started=on_verification_started,
    )

    return summary


async def _run_task_dir(
    *,
    task_dir: Path,
    task_name: str,
    evaluation_run_id: str,
    agent_path: Path,
    agent_timeout_sec: float | None,
    upstream_url: str,
    upstream_host: str,
    results_dir: Path,
    debug: bool,
    job_name: str | None,
    on_agent_started: TrialHook | None = None,
    on_verification_started: TrialHook | None = None,
) -> HarborRunSummary:
    """Build and execute the one-task Harbor job for a single evaluation run.

    `task_dir` is expected to already be the fully materialized Harbor task.
    """
    from harbor.environments.factory import EnvironmentFactory
    from harbor.job import Job
    from harbor.models.job.config import JobConfig, RetryConfig
    from harbor.models.trial.config import AgentConfig, EnvironmentConfig, TaskConfig

    resolved_job_name = job_name or f"{task_name}__{uuid4().hex[:8]}"
    job_dir = results_dir / resolved_job_name
    effective_timeout = agent_timeout_sec if agent_timeout_sec is not None and agent_timeout_sec > 0 else None
    ridges_trial_id = uuid4().hex

    agent_kwargs: dict[str, Any] = {
        "agent_path": str(agent_path),
    }
    agent_env = _harbor_agent_env(
        evaluation_run_id=evaluation_run_id,
        agent_timeout_sec=effective_timeout,
    )

    environment_config = EnvironmentConfig(
        env=docker_environment_env(
            ridges_trial_id=ridges_trial_id,
            upstream_url=upstream_url,
            upstream_host=upstream_host,
        )
    )
    config = JobConfig(
        job_name=resolved_job_name,
        jobs_dir=results_dir,
        n_attempts=1,
        debug=debug,
        n_concurrent_trials=1,
        quiet=True,
        retry=RetryConfig(max_retries=0),
        environment=environment_config,
        tasks=[TaskConfig(path=task_dir)],
        agents=[
            AgentConfig(
                import_path="ridges_harbor.agents:RidgesMinerAgent",
                override_timeout_sec=effective_timeout,
                kwargs=agent_kwargs,
                env=agent_env,
            )
        ],
    )
    enable_verifier_egress = build_enable_verifier_egress_hook(ridges_trial_id=ridges_trial_id)

    try:
        EnvironmentFactory.run_preflight(
            type=config.environment.type,
            import_path=config.environment.import_path,
        )
        job = await Job.create(config)

        if on_agent_started is not None:
            job.on_agent_started(on_agent_started)

        job.on_verification_started(enable_verifier_egress)

        if on_verification_started is not None:
            job.on_verification_started(on_verification_started)

        job_result = await job.run()
    except Exception as exception:
        error_path = _write_runner_exception(job_dir)
        job_log_path = job_dir / "job.log"
        log_hint = job_log_path if job_log_path.exists() else error_path
        raise RuntimeError(f"Harbor failed for {task_name}. See {log_hint}") from exception

    if len(job_result.trial_results) != 1:
        raise RuntimeError(
            f"Harbor job {resolved_job_name} returned {len(job_result.trial_results)} trial results; expected exactly 1"
        )

    trial_result = job_result.trial_results[0]
    trial_dir = job.job_dir / trial_result.trial_name
    summary = HarborRunSummary(
        trial_result=trial_result,
        task_name=task_name,
        job_dir=job.job_dir,
        task_dir=task_dir,
        trial_dir=trial_dir,
    )

    return summary
