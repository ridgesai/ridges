"""Local-only Harbor agent wrapper that mutates task images for convenience."""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import TYPE_CHECKING

from ridges_harbor.agents import RidgesMinerAgent

if TYPE_CHECKING:
    from harbor.environments.base import BaseEnvironment

LOCAL_RUNTIME_BOOTSTRAP_LOG_FILENAME = "local-runtime-bootstrap.log"
LOCAL_RUNTIME_BASELINE_REQUIREMENTS_PATH = Path(__file__).with_name("baseline-requirements.txt")


def _read_requirements(path: Path) -> tuple[str, ...]:
    """Load one requirement per non-comment line from a requirements file."""
    requirements: list[str] = []
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        requirements.append(line)
    return tuple(requirements)


def _requirement_to_distribution_name(requirement: str) -> str:
    """Best-effort distribution name for a baseline requirement."""
    return re.split(r"[<>=!~;\[]", requirement, maxsplit=1)[0].strip()


LOCAL_RUNTIME_MINER_PACKAGES = _read_requirements(LOCAL_RUNTIME_BASELINE_REQUIREMENTS_PATH)
LOCAL_RUNTIME_DISTRIBUTION_NAMES = tuple(
    _requirement_to_distribution_name(package) for package in LOCAL_RUNTIME_MINER_PACKAGES
)


def _build_local_runtime_bootstrap_command() -> str:
    """Build the best-effort shell command that ensures local miner baseline deps."""
    packages = " ".join(LOCAL_RUNTIME_MINER_PACKAGES)
    probe = (
        "import importlib.metadata as metadata\n"
        "import sys\n"
        f"dists={list(LOCAL_RUNTIME_DISTRIBUTION_NAMES)!r}\n"
        "missing=[]\n"
        "for dist in dists:\n"
        "    try:\n"
        "        metadata.version(dist)\n"
        "    except metadata.PackageNotFoundError:\n"
        "        missing.append(dist)\n"
        "sys.exit(0 if not missing else 1)\n"
    )
    return (
        f"python3 -c {shlex.quote(probe)}"
        " || ("
        "python3 -m pip --version >/dev/null 2>&1"
        " || python3 -m ensurepip --upgrade; "
        f"python3 -m pip install --no-cache-dir {packages}"
        f" || python3 -m pip install --break-system-packages --no-cache-dir {packages}"
        ")"
    )


class LocalMinerAgent(RidgesMinerAgent):
    """Local-testing agent that best-effort installs the common miner baseline."""

    async def _bootstrap_runtime_dependencies(self, environment: "BaseEnvironment") -> None:
        await super()._bootstrap_runtime_dependencies(environment)
        await self._exec_with_log(
            environment,
            executor=self.exec_as_root,
            command=_build_local_runtime_bootstrap_command(),
            log_filename=LOCAL_RUNTIME_BOOTSTRAP_LOG_FILENAME,
            cancelled_detail="command execution was cancelled",
            error_summary="Failed to install local miner baseline packages",
            error_type=RuntimeError,
        )
