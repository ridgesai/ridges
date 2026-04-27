"""Compute stable digests for pre-materialized Harbor task directories."""

from __future__ import annotations

import hashlib
import stat
from pathlib import Path

IGNORED_ARTIFACT_NAMES = {".DS_Store"}
IGNORED_ARTIFACT_SUFFIXES = {".pyc", ".pyo"}
IGNORED_ARTIFACT_PARTS = {"__pycache__"}


def is_ignored_artifact(path: Path) -> bool:
    """Return whether a file is local artifact noise, not task input."""

    return (
        path.name in IGNORED_ARTIFACT_NAMES
        or path.suffix in IGNORED_ARTIFACT_SUFFIXES
        or any(part in IGNORED_ARTIFACT_PARTS for part in path.parts)
    )


def compute_task_digest(task_dir: Path) -> str:
    """Hash the exact task directory Harbor will execute."""

    digest = hashlib.sha256()
    for path in sorted(p for p in task_dir.rglob("*") if p.is_file() and not is_ignored_artifact(p)):
        relative_path = path.relative_to(task_dir).as_posix()
        mode = stat.S_IMODE(path.stat().st_mode)
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(f"{mode:o}".encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"
