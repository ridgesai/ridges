"""Docker-specific Harbor runtime helpers for the proxy-sidecar scaffold."""

from __future__ import annotations

import asyncio
import asyncio.subprocess
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from harbor.environments.docker.docker import DockerEnvironment

from models.openrouter import OpenRouterRuntimeConfig

RIDGES_TRIAL_ID_LABEL = "ridges.trial_id"


class DockerStartGate:
    """Keeps the janitor's cache eviction from overlapping Docker environment starts.

    A start pulls digest-pinned sidecars and resolves build-cache base images
    before any container references them, so evicting either one mid-start fails
    the trial. Starts share the gate; eviction holds it exclusively, and new
    starts wait while eviction drains the in-flight ones.
    """

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._active_starts = 0
        self._exclusive = False

    @asynccontextmanager
    async def start(self) -> AsyncIterator[None]:
        async with self._condition:
            await self._condition.wait_for(lambda: not self._exclusive)
            self._active_starts += 1
        try:
            yield
        finally:
            async with self._condition:
                self._active_starts -= 1
                self._condition.notify_all()

    @asynccontextmanager
    async def exclusive(self, *, drain_timeout_sec: float) -> AsyncIterator[bool]:
        """Yield True once no start is in flight, or False (gate released) after the drain timeout."""
        async with self._condition:
            self._exclusive = True
            try:
                async with asyncio.timeout(drain_timeout_sec):
                    await self._condition.wait_for(lambda: self._active_starts == 0)
                drained = True
            except TimeoutError:
                drained = False
            if not drained:
                self._exclusive = False
                self._condition.notify_all()
        try:
            yield drained
        finally:
            if drained:
                async with self._condition:
                    self._exclusive = False
                    self._condition.notify_all()


docker_start_gate = DockerStartGate()


class RidgesDockerEnvironment(DockerEnvironment):
    """Harbor's Docker environment with every start registered on the shared start gate."""

    async def start(self, force_build: bool):
        async with docker_start_gate.start():
            await super().start(force_build)


@dataclass(slots=True, frozen=True)
class TrialDockerHandles:
    """The concrete Docker resources backing one Harbor trial."""

    container_id: str
    egress_network_id: str


TrialHook = Callable[[Any], Awaitable[None]]


class VerifierEgressSetupError(RuntimeError):
    """Raised when Ridges cannot widen verifier network access for a Harbor trial."""


async def _run_host_command(args: list[str], *, check: bool = True) -> tuple[int, str]:
    """Run a host-side Docker command and return its exit code plus combined output."""
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await process.communicate()
    output = stdout.decode(errors="replace") if stdout else ""
    code = process.returncode or 0

    if check and code != 0:
        raise RuntimeError(f"Host command failed ({code}): {' '.join(args)}\n{output}")

    return code, output


def _resolve_single_match(output: str, *, resource: str, trial_id: str) -> str:
    """Require exactly one Docker resource match to avoid ambiguous host mutations."""
    matches = [line.strip() for line in output.splitlines() if line.strip()]
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one {resource} for Harbor trial {trial_id}, found {len(matches)}")
    return matches[0]


async def _find_labeled_main_container_id(
    ridges_trial_id: str,
    *,
    trial_id: str,
) -> str:
    """Return the Harbor trial's `main` container id from Docker labels."""
    _, output = await _run_host_command(
        [
            "docker",
            "ps",
            "-q",
            "--filter",
            f"label={RIDGES_TRIAL_ID_LABEL}={ridges_trial_id}",
            "--filter",
            "label=com.docker.compose.service=main",
        ]
    )
    return _resolve_single_match(
        output,
        resource=f"main container labeled {RIDGES_TRIAL_ID_LABEL}={ridges_trial_id}",
        trial_id=trial_id,
    )


async def _find_labeled_egress_network_id(
    ridges_trial_id: str,
    *,
    trial_id: str,
) -> str:
    """Return the Harbor trial's `sandbox_egress` network id from Docker labels."""
    _, output = await _run_host_command(
        [
            "docker",
            "network",
            "ls",
            "-q",
            "--filter",
            f"label={RIDGES_TRIAL_ID_LABEL}={ridges_trial_id}",
            "--filter",
            "label=com.docker.compose.network=sandbox_egress",
        ]
    )
    return _resolve_single_match(
        output,
        resource=f"sandbox_egress network labeled {RIDGES_TRIAL_ID_LABEL}={ridges_trial_id}",
        trial_id=trial_id,
    )


async def _resolve_trial_docker_handles(
    *,
    harbor_trial_id: str,
    ridges_trial_id: str,
) -> TrialDockerHandles:
    """Resolve the container and egress network ids backing one Harbor trial."""
    return TrialDockerHandles(
        container_id=await _find_labeled_main_container_id(
            ridges_trial_id,
            trial_id=harbor_trial_id,
        ),
        egress_network_id=await _find_labeled_egress_network_id(
            ridges_trial_id,
            trial_id=harbor_trial_id,
        ),
    )


def build_enable_verifier_egress_hook(
    *,
    ridges_trial_id: str,
) -> TrialHook:
    """Build a Harbor on-verification-started hook that widens Docker egress.

    The returned hook attaches the trial's main container to the sandbox
    egress network so the verifier can reach external hosts.
    """

    async def enable_verifier_egress(event) -> None:
        """Resolve Docker handles at verification time, then widen egress for the verifier."""
        handles = await _resolve_trial_docker_handles(
            harbor_trial_id=event.trial_id,
            ridges_trial_id=ridges_trial_id,
        )

        code, output = await _run_host_command(
            [
                "docker",
                "network",
                "connect",
                handles.egress_network_id,
                handles.container_id,
            ],
            check=False,
        )
        normalized = output.lower()
        if code != 0 and "already exists" not in normalized and "already connected" not in normalized:
            raise VerifierEgressSetupError(
                f"Failed to connect verifier egress for Harbor trial {event.trial_id}:\n{output}"
            )

    return enable_verifier_egress


def docker_environment_env(
    *,
    ridges_trial_id: str,
    evaluation_run_id: str,
    max_cost_usd: str,
    proxy_data_dir: str,
    openrouter_config: OpenRouterRuntimeConfig | None,
    inference_seed: int | None = None,
) -> dict[str, str]:
    """Build Docker-scaffold env vars for one Harbor trial."""
    env = {
        "RIDGES_TRIAL_ID": ridges_trial_id,
        "RIDGES_EVALUATION_RUN_ID": evaluation_run_id,
        "RIDGES_MAX_COST_USD": max_cost_usd,
        "RIDGES_PROXY_DATA_DIR": proxy_data_dir,
        "RIDGES_INFERENCE_SEED": str(inference_seed) if inference_seed is not None else "",
        "RIDGES_OPENROUTER_MANAGEMENT_KEY": "",
        "RIDGES_OPENROUTER_WORKSPACE_ID": "",
        "RIDGES_OPENROUTER_EXPECTED_API_KEY_SHA256": "",
        "DOCKER_BUILDKIT": os.environ.get("DOCKER_BUILDKIT", "1"),
        "COMPOSE_DOCKER_CLI_BUILD": os.environ.get("COMPOSE_DOCKER_CLI_BUILD", "0"),
        "COMPOSE_BAKE": os.environ.get("COMPOSE_BAKE", "true"),
    }
    if openrouter_config is not None:
        env.update(openrouter_config.sidecar_env_vars())
    return env


async def prune_dangling_images() -> None:
    """Best-effort cleanup for dangling Docker images left by Harbor trials."""
    try:
        await _run_host_command(["docker", "image", "prune", "-f"], check=False)
    except Exception:
        pass
