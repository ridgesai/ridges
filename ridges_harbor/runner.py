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
from ridges_harbor._stdlib_contract import HARBOR_RUNNER_ERROR_FILENAME, PATCH_FILENAME
from ridges_harbor.digest import compute_task_digest
from ridges_harbor.docker_runtime import (
    TrialHook,
    build_enable_verifier_egress_hook,
    docker_environment_env,
)
from ridges_harbor.k8s_compose import parse_kubernetes_compose
from ridges_harbor.progress_logging import install_logging_harbor_progress
from ridges_harbor.shared import DEFAULT_RESULTS_DIR, HarborRunSummary

install_logging_harbor_progress()

DEFAULT_AGENT_SANDBOX_PROXY_URL = "http://sandbox-proxy:80"


def _load_single_step_task(task_dir: Path) -> Any:
    """Parse a materialized task archive, rejecting multi-step configs."""
    from harbor.models.task.task import Task

    task = Task(task_dir)
    if task.config.steps:
        raise RuntimeError("Ridges Harbor 0.20 execution currently supports only single-step tasks")
    return task


def _uses_separate_verifier(task_dir: Path) -> bool:
    """Resolve the task's native verifier mode using Harbor's own rules."""
    from harbor.models.task.verifier_mode import task_has_any_separate_verifier

    return task_has_any_separate_verifier(_load_single_step_task(task_dir).config)


def _resolve_separate_verifier(task_dir: Path) -> bool:
    """Resolve separate mode and preflight the archive its verifier image needs.

    Docker rejects a verifier environment without a build definition deep inside
    Harbor, while Kubernetes only notices in a BuildKit init container. Checking
    on the host makes both backends refuse the same archives. Separate tasks must
    also declare the published patch as their sole task-level artifact.
    """
    from harbor.constants import MAIN_SERVICE_NAME
    from harbor.environments.definition import has_agent_environment_definition
    from harbor.models.task.artifacts import effective_artifact_service, normalize_artifact_entries
    from harbor.models.task.verifier_mode import (
        resolve_effective_verifier_env_config,
        task_has_any_separate_verifier,
    )
    from harbor.models.trial.paths import EnvironmentPaths

    task = _load_single_step_task(task_dir)
    if not task_has_any_separate_verifier(task.config):
        return False

    patch_artifact = (EnvironmentPaths.agent_dir / PATCH_FILENAME).as_posix()
    artifacts = normalize_artifact_entries(task.config.artifacts)
    if (
        len(artifacts) != 1
        or artifacts[0].source != patch_artifact
        or effective_artifact_service(artifacts[0]) != MAIN_SERVICE_NAME
    ):
        raise RuntimeError(
            f"Separate-verifier task {task_dir} must declare exactly one task-level artifact: "
            f"{patch_artifact!r} from the main service"
        )

    verifier_env = resolve_effective_verifier_env_config(task.config, None)
    docker_image = verifier_env.docker_image if verifier_env is not None else None
    if not has_agent_environment_definition(task.paths.tests_dir, docker_image=docker_image):
        raise RuntimeError(
            f"Separate-verifier task {task_dir} has no verifier environment definition: add "
            "tests/Dockerfile, tests/docker-compose.yaml, or set the verifier "
            "[environment].docker_image"
        )
    if not task.paths.test_path.exists():
        raise RuntimeError(
            f"Separate-verifier task {task_dir} is missing tests/test.sh; Ridges bakes the test "
            "scripts into the verifier image instead of uploading them at verification time"
        )
    return True


def _validate_kubernetes_compose_file(compose_path: Path) -> None:
    """Parse one Compose file; leftover names may skip sidecar validation."""
    if not compose_path.exists():
        return
    parse_kubernetes_compose(compose_path)


def _validate_kubernetes_task_services(task_dir: Path, *, separate_verifier: bool = False) -> None:
    """Reject Compose features the Kubernetes adapter cannot translate."""
    _validate_kubernetes_compose_file(task_dir / "environment" / "docker-compose.yaml")
    tests_compose = task_dir / "tests" / "docker-compose.yaml"
    _validate_kubernetes_compose_file(tests_compose)
    if not separate_verifier and parse_kubernetes_compose(tests_compose):
        raise RuntimeError(
            "Kubernetes shared mode only runs environment/ sidecars; "
            "tests/docker-compose.yaml defines extra services. "
            'Set environment_mode = "separate" or use Docker.'
        )


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
    environment_build_timeout_multiplier: float | None = None,
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
        environment_build_timeout_multiplier=environment_build_timeout_multiplier,
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
    environment_build_timeout_multiplier: float | None = None,
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
    separate_verifier = _resolve_separate_verifier(task_dir)
    if ridges_environment_type == "kubernetes":
        _validate_kubernetes_task_services(task_dir, separate_verifier=separate_verifier)

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
        "separate_verifier": separate_verifier,
    }
    effective_max_cost_usd = str(max_cost_usd) if max_cost_usd is not None else "9"
    agent_env = _harbor_agent_env(
        evaluation_run_id=evaluation_run_id,
        max_cost_usd=effective_max_cost_usd,
        agent_timeout_sec=effective_timeout,
        openrouter_config=openrouter_config,
    )

    if ridges_environment_type == "kubernetes":
        # The proxy sidecar shares the pod network namespace and listens on 8080.
        agent_env["SANDBOX_PROXY_URL"] = "http://127.0.0.1:8080"

        from validator.config import (
            K8S_BUILD_REGISTRY,
            K8S_BUILD_REGISTRY_INSECURE,
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
            K8S_SIDECAR_MEMORY_LIMIT_MI,
            K8S_SIDECAR_MEMORY_REQUEST_MI,
            PROXY_IMAGE,
        )

        K8S_OWNER_POD_NAME = os.getenv("MY_POD_NAME")
        K8S_OWNER_POD_UID = os.getenv("MY_POD_UID")

        from ridges_harbor.k8s_environment import build_isolated_k8s_apis
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
                "inference_seed": inference_seed,
                "openrouter_sidecar_env": openrouter_config.sidecar_env_vars() if openrouter_config else {},
                "proxy_data_dir": str(proxy_data_dir),
                "task_dir": str(task_dir),
                "verifier_image_required": separate_verifier,
                "sidecar_memory_request_mi": K8S_SIDECAR_MEMORY_REQUEST_MI,
                "sidecar_memory_limit_mi": K8S_SIDECAR_MEMORY_LIMIT_MI,
                "kubeconfig_context": K8S_CONTEXT,
                "node_selector": K8S_NODE_SELECTOR,
                "labels": {"ridges.ai/trial-id": ridges_trial_id},
                "registry_credentials_secret": K8S_REGISTRY_SECRET,
                "registry_password": K8S_REGISTRY_PASSWORD,
                "registry_insecure": K8S_REGISTRY_INSECURE,
                "build_registry": K8S_BUILD_REGISTRY,
                "build_registry_insecure": K8S_BUILD_REGISTRY_INSECURE,
                "owner_pod_name": K8S_OWNER_POD_NAME,
                "owner_pod_uid": K8S_OWNER_POD_UID,
                "memory_limit_multiplier": K8S_MEMORY_LIMIT_MULTIPLIER,
                "memory_request_fraction": K8S_MEMORY_REQUEST_FRACTION,
                "cpu_request_fraction": K8S_CPU_REQUEST_FRACTION,
            },
        )

        core_api, _batch_api = build_isolated_k8s_apis(K8S_CONTEXT)

        enable_verifier_egress = build_k8s_verifier_egress_hook(
            namespace=K8S_NAMESPACE,
            core_api=core_api,
        )
    else:
        environment_config = EnvironmentConfig(
            env=docker_environment_env(
                ridges_trial_id=ridges_trial_id,
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
        artifacts=[],
        environment_build_timeout_multiplier=environment_build_timeout_multiplier,
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

        if not separate_verifier:
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
