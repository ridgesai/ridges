"""Docker-specific Harbor runtime helpers for the proxy-sidecar scaffold."""

from __future__ import annotations

import asyncio
import asyncio.subprocess
import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

RIDGES_TRIAL_ID_LABEL = "ridges.trial_id"


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
    upstream_url: str,
    upstream_host: str,
) -> dict[str, str]:
    """Build Docker-scaffold env vars for one Harbor trial."""
    return {
        "RIDGES_TRIAL_ID": ridges_trial_id,
        "RIDGES_HARBOR_UPSTREAM_URL": upstream_url,
        "RIDGES_HARBOR_UPSTREAM_HOST": upstream_host,
        "DOCKER_BUILDKIT": os.environ.get("DOCKER_BUILDKIT", "0"),
        "COMPOSE_DOCKER_CLI_BUILD": os.environ.get("COMPOSE_DOCKER_CLI_BUILD", "0"),
        "COMPOSE_BAKE": os.environ.get("COMPOSE_BAKE", "false"),
    }


async def prune_dangling_images() -> None:
    """Best-effort cleanup for dangling Docker images left by Harbor trials."""
    try:
        await _run_host_command(["docker", "image", "prune", "-f"], check=False)
    except Exception:
        pass
