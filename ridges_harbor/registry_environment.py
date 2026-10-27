"""
Optional task image cache for Docker; native Harbor remains the fallback.

Pulls authenticate with a short-lived, per-repository token from the registered
provider (the validator asks the platform API), or with the host Docker
configuration when no provider is registered. 
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import re
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from weakref import WeakValueDictionary

import yaml
from harbor.environments.docker.docker import DockerEnvironment

from ridges_harbor.task_image_contract import (
    DIGEST_RE,
    PLATFORMS,
    environment_builds,
    image_labels,
    image_ref,
    validate_registry,
)


def registry_environment_kwargs(task_digest: str) -> dict:
    """Opt in per host, optionally restricted to a pilot's full task digests."""
    registry = os.environ.get("RIDGES_TASK_IMAGE_REGISTRY", "").strip()
    if not registry:
        return {}

    validate_registry(registry)
    if not DIGEST_RE.fullmatch(task_digest):
        raise ValueError("Image cache requires a full task digest")

    selected = os.environ.get("RIDGES_TASK_IMAGE_DIGESTS", "").replace(",", " ").split()
    if any(not DIGEST_RE.fullmatch(digest) for digest in selected):
        raise ValueError("RIDGES_TASK_IMAGE_DIGESTS must contain full sha256 task digests")

    if selected and task_digest not in selected:
        return {}

    timeout = int(os.environ.get("RIDGES_TASK_IMAGE_PULL_TIMEOUT_SEC", "300"))
    if not 1 <= timeout <= 1800:
        raise ValueError("RIDGES_TASK_IMAGE_PULL_TIMEOUT_SEC must be between 1 and 1800")
    return {"task_image_registry": registry, "task_digest": task_digest, "image_pull_timeout_sec": timeout}


def registry_startup_timeouts(
    task_build_timeout_sec: float, build_timeout_multiplier: float | None, image_pull_timeout_sec: float
) -> tuple[float, float]:
    """Return native startup seconds and Harbor's multiplier including cache time.

    Harbor 0.20 uses the agent task's build timeout for both environment roles.
    Its default multiplier is 1.0 in the runner's one-task job.
    """
    multiplier = 1.0 if build_timeout_multiplier is None else build_timeout_multiplier
    native_timeout = task_build_timeout_sec * multiplier
    if task_build_timeout_sec <= 0 or not math.isfinite(native_timeout) or native_timeout <= 0:
        raise ValueError("Image cache requires a positive finite environment startup timeout")
    return native_timeout, (native_timeout + image_pull_timeout_sec) / task_build_timeout_sec


class ImageCacheUnavailable(RuntimeError):
    """A safe reason code, without command output or registry credentials."""


_token_provider: Callable[[str], Awaitable[str]] | None = None


def set_registry_token_provider(provider: Callable[[str], Awaitable[str]] | None) -> None:
    """Register an async repository -> pull token callable for this process."""
    global _token_provider
    _token_provider = provider


async def _pull_token(registry: str, reference: str) -> str | None:
    if _token_provider is None:
        return None

    repository = reference.rsplit(":", 1)[0].removeprefix(registry + "/")
    try:
        token = await _token_provider(repository)
    except Exception as exc:
        raise ImageCacheUnavailable("token_unavailable") from exc

    if not isinstance(token, str) or not token:
        raise ImageCacheUnavailable("token_unavailable")
    return token


@contextlib.contextmanager
def _pull_environment(registry: str, token: str | None):
    """Yield a subprocess env with a private, temporary registry token, or None for the host config."""
    if token is None:
        yield None
        return

    with tempfile.TemporaryDirectory(prefix="ridges-registry-auth-") as directory:
        (Path(directory) / "config.json").write_text(json.dumps({"auths": {registry: {"registrytoken": token}}}))
        yield {**os.environ, "DOCKER_CONFIG": directory}


def _pull_failure_reason(error: str, reference: str) -> str:
    """Classify a failed pull; raw registry output is never logged (it can contain URLs)."""
    repository = reference.rsplit(":", 1)[0].lower()
    diagnostic = re.sub(r"https?://\S+", "<url>", error.lower().replace(repository, "<image>"))

    if re.search(r"\b(?:unauthorized|denied|forbidden|401|403)\b|no basic auth credentials", diagnostic):
        return "pull_denied"

    if any(word in diagnostic for word in ("manifest unknown", "name unknown", "not found")):
        return "pull_missing"
    return "pull_failed"


async def _docker(*args: str, env: dict[str, str] | None = None) -> tuple[int, str]:
    """Return (code, stdout) on success or (code, stderr) on failure."""
    process = await asyncio.create_subprocess_exec(
        "docker", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env
    )
    try:
        stdout, stderr = await process.communicate()
    except BaseException:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await process.wait()
        raise
    return process.returncode, (stderr if process.returncode else stdout).decode(errors="replace")


def _verified_reference(info: dict, reference: str, labels: dict[str, str], platform: str) -> str:
    actual_labels = info.get("Config", {}).get("Labels", {}) or {}
    if any(actual_labels.get(key) != value for key, value in labels.items()):
        raise ImageCacheUnavailable("label_mismatch")

    if f"{info.get('Os')}/{info.get('Architecture')}" != platform:
        raise ImageCacheUnavailable("platform_mismatch")

    repository = reference.rsplit(":", 1)[0]
    for digest_ref in info.get("RepoDigests", []) or []:
        if digest_ref.startswith(repository + "@") and DIGEST_RE.fullmatch(digest_ref.split("@", 1)[1]):
            return digest_ref
    raise ImageCacheUnavailable("missing_repo_digest")


class RegistryDockerEnvironment(DockerEnvironment):
    """Select prebuilt images before starting containers, independently per role."""

    _pull_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()

    def __init__(
        self,
        *args,
        task_image_registry: str,
        task_digest: str,
        native_startup_timeout_sec: float,
        image_pull_timeout_sec: int = 300,
        **kwargs,
    ):
        self._registry_compose_path: Path | None = None
        self._registry_temp_dir: tempfile.TemporaryDirectory | None = None
        self._task_image_registry = validate_registry(task_image_registry)
        self._task_digest = task_digest
        self._image_pull_timeout_sec = image_pull_timeout_sec
        self._native_startup_timeout_sec = native_startup_timeout_sec
        super().__init__(*args, **kwargs)
        self._original_task_env_config = self.task_env_config
        self._original_prebuilt_image_name = self._env_vars.prebuilt_image_name

    def _reset_registry_selection(self):
        self.task_env_config = self._original_task_env_config
        self._env_vars.prebuilt_image_name = self._original_prebuilt_image_name
        self._registry_compose_path = None
        if self._registry_temp_dir is not None:
            self._registry_temp_dir.cleanup()
            self._registry_temp_dir = None

    @property
    def _environment_docker_compose_path(self) -> Path:
        return self._registry_compose_path or super()._environment_docker_compose_path

    async def _resolve_image(self, reference: str, labels: dict[str, str], platform: str) -> tuple[str, str]:
        lock = self._pull_locks.setdefault(reference, asyncio.Lock())
        async with lock:
            code, output = await _docker("image", "inspect", reference)
            status = "cached"
            if code:
                token = await _pull_token(self._task_image_registry, reference)
                with _pull_environment(self._task_image_registry, token) as env:
                    code, error = await _docker("pull", "--platform", platform, reference, env=env)
                if code:
                    raise ImageCacheUnavailable(_pull_failure_reason(error, reference))

                code, output = await _docker("image", "inspect", reference)
                if code:
                    raise ImageCacheUnavailable("inspect_failed")
                status = "pulled"

            info = json.loads(output)
            if not isinstance(info, list) or len(info) != 1 or not isinstance(info[0], dict):
                raise ImageCacheUnavailable("invalid_inspect_result")
            return _verified_reference(info[0], reference, labels, platform), status

    async def _prepare_images(self) -> tuple[str, list[tuple[str, str, str]]]:
        role = {"environment": "agent", "tests": "verifier"}.get(self.environment_dir.name)
        if role is None or self.extra_docker_compose_paths or self._is_windows_container:
            raise ImageCacheUnavailable("unsupported_environment")

        builds = environment_builds(self.environment_dir, self.task_env_config.docker_image)
        if not builds:
            return role, []

        code, platform = await _docker("version", "--format", "{{.Server.Os}}/{{.Server.Arch}}")
        platform = platform.strip()
        if code or platform not in PLATFORMS:
            raise ImageCacheUnavailable("unsupported_daemon_platform")

        if os.environ.get("DOCKER_DEFAULT_PLATFORM", platform) != platform:
            raise ImageCacheUnavailable("platform_override")

        images = {}
        outcomes = []
        for service in sorted(builds):
            reference = image_ref(self._task_image_registry, self._task_digest, role, service, platform)
            pinned, status = await self._resolve_image(
                reference, image_labels(self._task_digest, role, service), platform
            )
            images[service] = pinned
            outcomes.append((service, pinned, status))

        original_compose = super()._environment_docker_compose_path
        compose = yaml.safe_load(original_compose.read_text()) if original_compose.exists() else {"services": {}}
        for service, pinned in images.items():
            config = compose["services"].setdefault(service, {})
            config.pop("build", None)
            config["image"] = pinned
            config["pull_policy"] = "never"
        self._registry_temp_dir = tempfile.TemporaryDirectory(prefix="ridges-task-images-")
        path = Path(self._registry_temp_dir.name) / "docker-compose.yaml"
        path.write_text(yaml.safe_dump(compose, sort_keys=False))

        if "main" in images:
            self.task_env_config = self.task_env_config.model_copy(update={"docker_image": images["main"]})
            self._env_vars.prebuilt_image_name = images["main"]
        self._registry_compose_path = path
        return role, outcomes

    async def start(self, force_build: bool):
        self._reset_registry_selection()
        started = time.monotonic()
        if not force_build:
            try:
                async with asyncio.timeout(self._image_pull_timeout_sec):
                    role, outcomes = await self._prepare_images()
                for service, pinned, status in outcomes:
                    self.logger.info(
                        "task_image_cache role=%s service=%s status=%s image=%s", role, service, status, pinned
                    )
            except (OSError, ValueError, RuntimeError, TimeoutError, yaml.YAMLError) as exc:
                self._reset_registry_selection()
                reason = str(exc) if isinstance(exc, ImageCacheUnavailable) else type(exc).__name__
                self.logger.warning("task_image_cache status=fallback reason=%s", reason)
        else:
            self.logger.info("task_image_cache status=bypassed reason=force_build")
        self.logger.info("task_image_cache preparation_seconds=%.3f", time.monotonic() - started)
        native_deadline = asyncio.timeout(self._native_startup_timeout_sec)
        try:
            async with native_deadline:
                await super().start(force_build=force_build)

        except TimeoutError as exc:
            if native_deadline.expired():
                exc.add_note(f"Native Docker startup timed out after {self._native_startup_timeout_sec:g} seconds")
                self.logger.error(
                    "task_image_cache status=timeout phase=native_startup timeout_seconds=%g",
                    self._native_startup_timeout_sec,
                )
            raise

    async def stop(self, delete: bool):
        try:
            await super().stop(delete=delete)
        finally:
            self._reset_registry_selection()
