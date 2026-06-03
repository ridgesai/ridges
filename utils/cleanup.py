"""Fail-safe, age-based pruning of local directories.

A single generic helper used to reclaim disk from the validator's two unbounded
local stores (the task cache and Harbor job artifacts). Pruning is deliberately
conservative: it only ever removes immediate child directories whose modification
time is older than a retention window, never touches in-flight work (dotfiles,
temp dirs, or names in ``exclude_names``), and never raises — a single
undeletable directory is logged and skipped so the rest of the sweep proceeds.

The functions here are synchronous/blocking by design; callers running on an
event loop should invoke them via ``asyncio.to_thread``.
"""

import logging
import shutil
import time
from collections.abc import Iterable
from pathlib import Path

logger = logging.getLogger(__name__)


def prune_dirs_older_than(
    parent: Path,
    max_age_seconds: float,
    *,
    exclude_names: Iterable[str] = frozenset(),
    skip_hidden: bool = True,
) -> int:
    """Delete immediate child directories of ``parent`` older than the window.

    Returns the number of directories removed. Never raises: a missing parent
    yields 0, and per-directory failures are logged and skipped.

    Parameters
    ----------
    parent : Path
        Parent directory whose immediate child directories are candidates for deletion.
    max_age_seconds : float
        Max amount of seconds since the directory was last modified.
    exclude_names : Iterable[str], optional
        Names to exclude from deletion, by default frozenset()
    skip_hidden : bool, optional
        Whether to skip hidden directories, by default True

    Returns
    -------
    int
        Number of directories removed
    """
    if not parent.exists():
        return 0

    excluded = set(exclude_names)
    now = time.time()
    removed = 0

    for path in sorted(parent.iterdir()):
        if not path.is_dir():
            continue
        if skip_hidden and path.name.startswith("."):
            continue
        if path.name in excluded:
            continue

        try:
            age_seconds = now - path.stat().st_mtime
        except OSError as exc:
            logger.warning(f"Cleanup: could not stat {path}, skipping: {exc}")
            continue
        if age_seconds < max_age_seconds:
            continue

        try:
            logger.debug(f"Cleanup: removing {path} (age {age_seconds:.1f}s exceeds {max_age_seconds:.1f}s)")
            shutil.rmtree(path, ignore_errors=False)
            removed += 1
        except OSError as exc:
            logger.warning(f"Cleanup: failed to remove {path} (best-effort), skipping: {exc}")

    return removed
