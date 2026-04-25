"""Relaxed local Harbor runner for miner testing."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
import tarfile
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from uuid import uuid4

from miners.inference_client import LocalInferenceConfig
from ridges_harbor._stdlib_contract import HARBOR_RUNNER_ERROR_FILENAME
from ridges_harbor.digest import compute_task_digest
from ridges_harbor.docker_runtime import prune_dangling_images
from ridges_harbor.shared import DEFAULT_RESULTS_DIR, HarborRunSummary

_ARCHIVE_SUFFIXES = (".tar.gz", ".tgz")
_IGNORED_TOP_LEVEL_NAMES = {"__MACOSX", ".DS_Store"}
_TASK_STAGING_DIRNAME = "_task_staging"

logger = logging.getLogger(__name__)


def _normalize_endpoint_url(url: str, *, label: str) -> str:
    stripped = url.strip().rstrip("/")
    parsed = urlparse(stripped)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"{label} must include a scheme and host: {url}")
    if parsed.params or parsed.query or parsed.fragment:
        raise ValueError(f"{label} must not include params/query/fragment: {url}")
    return stripped


@dataclass(frozen=True, slots=True)
class CustomSandboxProxyConfig:
    """Advanced local-only config for a sandbox-proxy-compatible inference endpoint."""

    sandbox_proxy_url: str
    provider: Literal["custom"] = "custom"

    def normalized(self) -> "CustomSandboxProxyConfig":
        return CustomSandboxProxyConfig(
            sandbox_proxy_url=_normalize_endpoint_url(
                self.sandbox_proxy_url,
                label="Custom sandbox proxy URL",
            )
        )

    def to_env(self) -> dict[str, str]:
        normalized = self.normalized()
        return {"SANDBOX_PROXY_URL": normalized.sandbox_proxy_url}


LocalRunInferenceConfig = LocalInferenceConfig | CustomSandboxProxyConfig


def _write_local_runner_exception(job_dir: Path) -> Path:
    """Write the current traceback next to the Harbor job output."""
    job_dir.mkdir(parents=True, exist_ok=True)
    error_path = job_dir / HARBOR_RUNNER_ERROR_FILENAME
    error_path.write_text(traceback.format_exc())
    return error_path


def task_staging_cache_dir(results_dir: str | Path | None = DEFAULT_RESULTS_DIR) -> Path:
    """Return the directory used for cached extracted task archives."""
    return Path(results_dir or DEFAULT_RESULTS_DIR).expanduser().resolve() / _TASK_STAGING_DIRNAME


def list_task_staging_cache_dirs(
    results_dir: str | Path | None = DEFAULT_RESULTS_DIR,
    *,
    max_age_seconds: float | None = None,
) -> list[Path]:
    """Return cached extracted task directories, optionally filtered by age."""
    staging_root = task_staging_cache_dir(results_dir)
    if not staging_root.exists():
        return []

    now = time.time()
    cached_dirs: list[Path] = []
    for path in sorted(staging_root.iterdir()):
        if not path.is_dir():
            continue
        if max_age_seconds is not None:
            age_seconds = now - path.stat().st_mtime
            if age_seconds < max_age_seconds:
                continue
        cached_dirs.append(path)
    return cached_dirs


def prune_task_staging_cache(
    results_dir: str | Path | None = DEFAULT_RESULTS_DIR,
    *,
    max_age_seconds: float | None = None,
) -> list[Path]:
    """Delete cached extracted task archives and return the removed directories."""
    removed: list[Path] = []
    for path in list_task_staging_cache_dirs(results_dir, max_age_seconds=max_age_seconds):
        shutil.rmtree(path, ignore_errors=False)
        removed.append(path)
    return removed


def _is_task_archive(task_path: Path) -> bool:
    """Return whether the input path is a supported local task archive."""
    return task_path.name.endswith(_ARCHIVE_SUFFIXES)


def _default_task_name(task_path: Path) -> str:
    """Infer a Harbor task name from a local directory or archive path."""
    if task_path.is_dir():
        return task_path.name
    for suffix in _ARCHIVE_SUFFIXES:
        if task_path.name.endswith(suffix):
            return task_path.name[: -len(suffix)]
    return task_path.stem


def _meaningful_entries(directory: Path) -> list[Path]:
    """Return top-level extracted entries, skipping common archive junk."""
    return sorted(path for path in directory.iterdir() if path.name not in _IGNORED_TOP_LEVEL_NAMES)


def _is_harbor_task_root(path: Path) -> bool:
    """Return whether a directory looks like a Harbor task root."""
    if not path.is_dir():
        return False
    return (path / "instruction.md").is_file() and (path / "task.toml").is_file() and (path / "environment").is_dir()


def _resolve_task_root(task_dir: Path) -> Path:
    """Accept either a Harbor task root or a one-level container directory."""
    if _is_harbor_task_root(task_dir):
        return task_dir

    entries = _meaningful_entries(task_dir)
    candidate_dirs = [path for path in entries if _is_harbor_task_root(path)]
    if len(candidate_dirs) == 1:
        return candidate_dirs[0]
    if len(candidate_dirs) > 1:
        candidates = ", ".join(str(path) for path in candidate_dirs)
        raise RuntimeError(f"Multiple Harbor task roots found under {task_dir}: {candidates}")

    raise RuntimeError(
        f"Local Harbor task path is not a Harbor task root: {task_dir}. "
        "Expected instruction.md, task.toml, and environment/, or a directory containing exactly one such child."
    )


def _extract_task_archive(task_archive: Path, *, staging_dir: Path) -> Path:
    """Extract a local task archive into a durable staging directory."""
    staging_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(task_archive, "r:gz") as archive:
        archive.extractall(staging_dir, filter="data")
    return _resolve_task_root(staging_dir)


def _archive_cache_key(task_archive: Path) -> str:
    """Return a stable cache key for a local task archive."""
    digest = hashlib.sha256()
    with task_archive.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_archive_to_cache(task_archive: Path, *, staging_root: Path) -> Path:
    """Extract a task archive into a content-addressed cache directory."""
    staging_root.mkdir(parents=True, exist_ok=True)
    cache_key = _archive_cache_key(task_archive)
    cache_dir = staging_root / f"sha256_{cache_key}"

    if cache_dir.exists():
        try:
            return _resolve_task_root(cache_dir)
        except RuntimeError:
            shutil.rmtree(cache_dir, ignore_errors=True)

    temp_dir = staging_root / f".tmp-{cache_key}-{uuid4().hex[:8]}"
    try:
        _extract_task_archive(task_archive, staging_dir=temp_dir)
        try:
            temp_dir.rename(cache_dir)
        except FileExistsError:
            shutil.rmtree(temp_dir, ignore_errors=True)
        return _resolve_task_root(cache_dir)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def _prepare_local_task_dir(
    task_path: Path,
    *,
    results_dir: Path,
) -> Path:
    """Resolve a local task directory or extract an archive into results staging."""
    if not task_path.exists():
        raise FileNotFoundError(f"Local Harbor task path does not exist: {task_path}")

    if task_path.is_dir():
        return _resolve_task_root(task_path)

    if not _is_task_archive(task_path):
        raise ValueError(f"Local Harbor task path must be a directory, .tar.gz, or .tgz: {task_path}")

    return _extract_archive_to_cache(task_path, staging_root=task_staging_cache_dir(results_dir))


async def _verify_task_digest(task_dir: Path, *, task_name: str, task_digest: str) -> None:
    """Verify a local task directory matches the expected content digest."""
    actual_digest = await asyncio.to_thread(compute_task_digest, task_dir)
    if actual_digest != task_digest:
        raise RuntimeError(f"Harbor task digest mismatch for {task_name}: expected {task_digest}, got {actual_digest}")


def _local_agent_env(
    *,
    evaluation_run_id: str,
    inference: LocalRunInferenceConfig,
    agent_timeout_sec: float | None,
) -> dict[str, str]:
    """Build the miner-facing environment for relaxed local runs."""
    env = {
        "EVALUATION_RUN_ID": evaluation_run_id,
        **inference.normalized().to_env(),
    }
    if agent_timeout_sec is not None:
        timeout = float(str(agent_timeout_sec).strip())
        if timeout > 0:
            env["AGENT_TIMEOUT"] = str(int(timeout)) if timeout.is_integer() else str(timeout)
    return env


async def run_local_task(
    task_path: str | Path,
    *,
    agent_path: str | Path,
    inference: LocalRunInferenceConfig,
    task_name: str | None = None,
    task_digest: str | None = None,
    evaluation_run_id: str | None = None,
    agent_timeout_sec: float | None = None,
    results_dir: str | Path | None = DEFAULT_RESULTS_DIR,
    debug: bool = False,
    job_name: str | None = None,
) -> HarborRunSummary:
    """Run a local Harbor task for miner testing without validator scaffold assumptions."""
    from harbor.environments.factory import EnvironmentFactory
    from harbor.job import Job
    from harbor.models.job.config import JobConfig, RetryConfig
    from harbor.models.trial.config import AgentConfig, EnvironmentConfig, TaskConfig

    resolved_task_path = Path(task_path).expanduser().resolve()
    resolved_agent_path = Path(agent_path).expanduser().resolve()
    if not resolved_agent_path.exists():
        raise FileNotFoundError(f"Miner agent file not found: {resolved_agent_path}")
    if not resolved_agent_path.is_file():
        raise IsADirectoryError(f"Miner agent path must be a file: {resolved_agent_path}")

    resolved_results_dir = Path(results_dir or DEFAULT_RESULTS_DIR).expanduser().resolve()
    resolved_results_dir.mkdir(parents=True, exist_ok=True)
    normalized_inference = inference.normalized()

    effective_task_name = task_name or _default_task_name(resolved_task_path)
    effective_task_dir = _prepare_local_task_dir(
        resolved_task_path,
        results_dir=resolved_results_dir,
    )
    if task_digest is not None:
        await _verify_task_digest(effective_task_dir, task_name=effective_task_name, task_digest=task_digest)

    effective_evaluation_run_id = evaluation_run_id or str(uuid4())
    effective_timeout = agent_timeout_sec if agent_timeout_sec is not None and agent_timeout_sec > 0 else None
    resolved_job_name = job_name or f"{effective_task_name}__{uuid4().hex[:8]}"
    job_dir = resolved_results_dir / resolved_job_name

    config = JobConfig(
        job_name=resolved_job_name,
        jobs_dir=resolved_results_dir,
        n_attempts=1,
        debug=debug,
        n_concurrent_trials=1,
        quiet=True,
        retry=RetryConfig(max_retries=0),
        environment=EnvironmentConfig(env={}),
        tasks=[TaskConfig(path=effective_task_dir)],
        agents=[
            AgentConfig(
                import_path="miners.local_agent:LocalMinerAgent",
                override_timeout_sec=effective_timeout,
                kwargs={"agent_path": str(resolved_agent_path)},
                env=_local_agent_env(
                    evaluation_run_id=effective_evaluation_run_id,
                    inference=normalized_inference,
                    agent_timeout_sec=effective_timeout,
                ),
            )
        ],
    )

    try:
        EnvironmentFactory.run_preflight(
            type=config.environment.type,
            import_path=config.environment.import_path,
        )
        job = await Job.create(config)
        job_result = await job.run()
    except Exception as exception:
        error_path = _write_local_runner_exception(job_dir)
        job_log_path = job_dir / "job.log"
        log_hint = job_log_path if job_log_path.exists() else error_path
        raise RuntimeError(f"Harbor failed for {effective_task_name}. See {log_hint}") from exception
    finally:
        try:
            await prune_dangling_images()
        except Exception as exception:
            logger.warning("Failed to prune dangling Docker images after local Harbor run: %s", exception)

    if len(job_result.trial_results) != 1:
        raise RuntimeError(
            f"Harbor job {resolved_job_name} returned {len(job_result.trial_results)} trial results; expected exactly 1"
        )

    trial_result = job_result.trial_results[0]
    trial_dir = job.job_dir / trial_result.trial_name
    return HarborRunSummary(
        trial_result=trial_result,
        task_name=effective_task_name,
        job_dir=job.job_dir,
        task_dir=effective_task_dir,
        trial_dir=trial_dir,
    )
