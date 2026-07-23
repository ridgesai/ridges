"""Turn a promoted Harbor execution spec into a one-task Harbor job."""

from __future__ import annotations

import asyncio
import os
import traceback
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from models.openrouter import OpenRouterRuntimeConfig
from ridges_harbor._stdlib_contract import HARBOR_RUNNER_ERROR_FILENAME
from ridges_harbor.digest import compute_task_digest
from ridges_harbor.docker_runtime import (
    TrialHook,
    build_enable_verifier_egress_hook,
    docker_environment_env,
)
from ridges_harbor.progress_logging import install_logging_harbor_progress
from ridges_harbor.shared import DEFAULT_RESULTS_DIR, HarborRunSummary, resolve_inference_gateway

install_logging_harbor_progress()

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
    max_cost_usd: str,
    agent_timeout_sec: float | None,
    openrouter_config: OpenRouterRuntimeConfig | None = None,
) -> dict[str, str]:
    """Build the env dict Harbor merges into every agent command."""
    normalized_timeout: str | None = None
    if agent_timeout_sec is not None:
        timeout = float(str(agent_timeout_sec).strip())
        if timeout > 0:
            normalized_timeout = str(int(timeout)) if timeout.is_integer() else str(timeout)

    env = {
        "EVALUATION_RUN_ID": evaluation_run_id,
        "RIDGES_MAX_COST_USD": max_cost_usd,
        "SANDBOX_PROXY_URL": DEFAULT_AGENT_SANDBOX_PROXY_URL,
    }
    if normalized_timeout is not None:
        env["AGENT_TIMEOUT"] = normalized_timeout
    if openrouter_config is not None:
        env.update(openrouter_config.agent_env_vars())
    return env


async def run_task(
    task_dir: str | Path,
    *,
    task_name: str,
    task_digest: str,
    evaluation_run_id: str,
    agent_path: str | Path,
    agent_timeout_sec: float | None = None,
    verifier_timeout_sec: float | None = None,
    inference_url: str | None = None,
    results_dir: str | Path | None = DEFAULT_RESULTS_DIR,
    debug: bool = False,
    job_name: str | None = None,
    openrouter_config: OpenRouterRuntimeConfig | None = None,
    max_cost_usd: float | None = None,
    fetch_task_url: Callable[[str], Awaitable[str]] | None = None,
    inference_seed: int | None = None,
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
        task_digest=task_digest,
        evaluation_run_id=evaluation_run_id,
        agent_path=resolved_agent_path,
        agent_timeout_sec=agent_timeout_sec,
        verifier_timeout_sec=verifier_timeout_sec,
        upstream_url=upstream_url,
        upstream_host=upstream_host,
        results_dir=resolved_results_dir,
        debug=debug,
        job_name=job_name,
        openrouter_config=openrouter_config,
        max_cost_usd=max_cost_usd,
        fetch_task_url=fetch_task_url,
        inference_seed=inference_seed,
        on_agent_started=on_agent_started,
        on_verification_started=on_verification_started,
    )

    return summary


async def _run_task_dir(
    *,
    task_dir: Path,
    task_name: str,
    task_digest: str = "",
    evaluation_run_id: str,
    agent_path: Path,
    agent_timeout_sec: float | None,
    verifier_timeout_sec: float | None,
    upstream_url: str,
    upstream_host: str,
    results_dir: Path,
    debug: bool,
    job_name: str | None,
    openrouter_config: OpenRouterRuntimeConfig | None = None,
    max_cost_usd: float | None = None,
    inference_seed: int | None = None,
    fetch_task_url: Callable[[str], Awaitable[str]] | None = None,
    on_agent_started: TrialHook | None = None,
    on_verification_started: TrialHook | None = None,
) -> HarborRunSummary:
    """Build and execute the one-task Harbor job for a single evaluation run.

    `task_dir` is expected to already be the fully materialized Harbor task.
    """
    from harbor.environments.factory import EnvironmentFactory
    from harbor.job import Job
    from harbor.models.job.config import JobConfig, RetryConfig
    from harbor.models.trial.config import AgentConfig, EnvironmentConfig, TaskConfig, VerifierConfig

    ridges_environment_type = os.getenv("RIDGES_ENVIRONMENT_TYPE", "docker")

    resolved_job_name = job_name or f"{task_name}__{uuid4().hex[:8]}"
    job_dir = results_dir / resolved_job_name
    effective_timeout = agent_timeout_sec if agent_timeout_sec is not None and agent_timeout_sec > 0 else None
    effective_verifier_timeout = (
        verifier_timeout_sec if verifier_timeout_sec is not None and verifier_timeout_sec > 0 else None
    )
    ridges_trial_id = uuid4().hex
    proxy_data_dir = job_dir / "proxy_data"
    proxy_data_dir.mkdir(parents=True, exist_ok=True)

    agent_kwargs: dict[str, Any] = {
        "agent_path": str(agent_path),
    }
    effective_max_cost_usd = str(max_cost_usd) if max_cost_usd is not None else "9"
    agent_env = _harbor_agent_env(
        evaluation_run_id=evaluation_run_id,
        max_cost_usd=effective_max_cost_usd,
        agent_timeout_sec=effective_timeout,
        openrouter_config=openrouter_config,
    )

    if ridges_environment_type == "kubernetes":
        # K8s proxy listens on 8080 (non-root can't bind to 80).
        agent_env["SANDBOX_PROXY_URL"] = "http://sandbox-proxy:8080"

        from kubernetes import client as k8s_client_mod
        from kubernetes import config as k8s_config_mod

        from validator.config import (
            K8S_CONTEXT,
            K8S_CPU_REQUEST_FRACTION,
            K8S_MEMORY_LIMIT_MULTIPLIER,
            K8S_MEMORY_REQUEST_FRACTION,
            K8S_NAMESPACE,
            K8S_NODE_SELECTOR,
            K8S_REGISTRY,
            K8S_REGISTRY_INSECURE,
            K8S_REGISTRY_PASSWORD,
            K8S_REGISTRY_SECRET,
            PROXY_IMAGE,
        )

        K8S_OWNER_POD_NAME = os.getenv("MY_POD_NAME")
        K8S_OWNER_POD_UID = os.getenv("MY_POD_UID")

        from ridges_harbor.k8s_runtime import build_k8s_verifier_egress_hook

        digest_tag = task_digest.split(":")[1][:12]

        # Generate a fresh presigned URL for the build Job's init container (5-min TTL).
        if fetch_task_url is None:
            raise RuntimeError("fetch_task_url callback is required in Kubernetes mode")
        presigned_url = await fetch_task_url(task_digest)

        environment_config = EnvironmentConfig(
            import_path="ridges_harbor.k8s_environment:RidgesKubernetesEnvironment",
            env={},
            kwargs={
                "namespace": K8S_NAMESPACE,
                "registry": K8S_REGISTRY,
                "task_name": task_name,
                "digest_tag": digest_tag,
                "task_archive_presigned_url": presigned_url,
                "proxy_image": PROXY_IMAGE,
                "evaluation_run_id": evaluation_run_id,
                "max_cost_usd": str(max_cost_usd) if max_cost_usd is not None else "999999",
                "openrouter_sidecar_env": openrouter_config.sidecar_env_vars() if openrouter_config else {},
                "proxy_data_dir": str(proxy_data_dir),
                "kubeconfig_context": K8S_CONTEXT,
                "node_selector": K8S_NODE_SELECTOR,
                "labels": {"ridges.ai/trial-id": ridges_trial_id},
                "registry_credentials_secret": K8S_REGISTRY_SECRET,
                "registry_password": K8S_REGISTRY_PASSWORD,
                "registry_insecure": K8S_REGISTRY_INSECURE,
                "owner_pod_name": K8S_OWNER_POD_NAME,
                "owner_pod_uid": K8S_OWNER_POD_UID,
                "memory_limit_multiplier": K8S_MEMORY_LIMIT_MULTIPLIER,
                "memory_request_fraction": K8S_MEMORY_REQUEST_FRACTION,
                "cpu_request_fraction": K8S_CPU_REQUEST_FRACTION,
            },
        )

        # Build k8s client for the egress hook
        try:
            k8s_config_mod.load_incluster_config()
        except k8s_config_mod.ConfigException:
            k8s_config_mod.load_kube_config(context=K8S_CONTEXT)
        core_api = k8s_client_mod.CoreV1Api()

        enable_verifier_egress = build_k8s_verifier_egress_hook(
            namespace=K8S_NAMESPACE,
            core_api=core_api,
        )
    else:
        environment_config = EnvironmentConfig(
            env=docker_environment_env(
                ridges_trial_id=ridges_trial_id,
                upstream_url=upstream_url,
                upstream_host=upstream_host,
                evaluation_run_id=evaluation_run_id,
                max_cost_usd=effective_max_cost_usd,
                proxy_data_dir=str(proxy_data_dir),
                openrouter_config=openrouter_config,
                inference_seed=inference_seed,
            )
        )
        enable_verifier_egress = build_enable_verifier_egress_hook(ridges_trial_id=ridges_trial_id)

    job_config = JobConfig(
        job_name=resolved_job_name,
        jobs_dir=results_dir,
        n_attempts=1,
        debug=debug,
        n_concurrent_trials=1,
        quiet=True,
        retry=RetryConfig(max_retries=0),
        environment=environment_config,
        verifier=VerifierConfig(max_timeout_sec=effective_verifier_timeout),
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

    try:
        EnvironmentFactory.run_preflight(
            type=job_config.environment.type,
            import_path=job_config.environment.import_path,
        )
        job = await Job.create(job_config)

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
