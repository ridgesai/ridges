"""Content-addressed cache for pre-materialized Harbor task archives.

Tasks are downloaded via presigned S3 URLs and stored locally by digest.
Once cached, a task is never re-downloaded — the digest guarantees immutability.
"""

from __future__ import annotations

import shutil
import tarfile
from pathlib import Path
from uuid import uuid4

import httpx

import utils.logger as logger
from ridges_harbor.digest import compute_task_digest

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "ridges" / "tasks"


def _cache_dir_for_digest(digest: str, *, cache_root: Path = DEFAULT_CACHE_DIR) -> Path:
    safe_digest = digest.replace(":", "_")
    return cache_root / safe_digest


def _cached_task_dir_for_name(task_name: str, digest: str, *, cache_root: Path = DEFAULT_CACHE_DIR) -> Path:
    return _cache_dir_for_digest(digest, cache_root=cache_root) / task_name


def _resolved_extracted_task_dir(extract_dir: Path) -> Path:
    """Return the extracted task root used for digest verification."""
    entries = [entry for entry in extract_dir.iterdir() if not entry.name.startswith(".")]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return extract_dir


def _stage_extracted_task_dir(
    extract_dir: Path,
    *,
    source_task_dir: Path,
    task_name: str,
) -> tuple[Path, Path]:
    """Stage the extracted task contents under ``<digest>/<task_name>/``."""
    staged_digest_dir = extract_dir.parent / "staged"
    staged_task_dir = staged_digest_dir / task_name
    staged_digest_dir.mkdir()

    if source_task_dir != extract_dir:
        source_task_dir.rename(staged_task_dir)
        return staged_digest_dir, staged_task_dir

    staged_task_dir.mkdir()
    for entry in extract_dir.iterdir():
        entry.rename(staged_task_dir / entry.name)

    return staged_digest_dir, staged_task_dir


def _cached_task_dirs_for_digest(
    task_digest: str,
    *,
    cache_root: Path = DEFAULT_CACHE_DIR,
) -> list[Path]:
    cached_dir = _cache_dir_for_digest(task_digest, cache_root=cache_root)
    if not cached_dir.exists():
        return []

    return sorted(entry for entry in cached_dir.iterdir() if entry.is_dir() and not entry.name.startswith("."))


def _resolve_cached_task_dir(
    task_name: str,
    task_digest: str,
    *,
    cache_root: Path = DEFAULT_CACHE_DIR,
) -> Path | None:
    """Resolve an already-cached task directory by digest, preferring task name."""
    cached_task_dir = _cached_task_dir_for_name(task_name, task_digest, cache_root=cache_root)
    if cached_task_dir.exists():
        return cached_task_dir

    task_dirs = _cached_task_dirs_for_digest(task_digest, cache_root=cache_root)
    if not task_dirs:
        return None

    chosen = task_dirs[0]
    available_names = ", ".join(task_dir.name for task_dir in task_dirs)
    logger.warning(
        f"Task cache digest {task_digest} requested as {task_name}, "
        f"reusing cached task {chosen.name} (available: {available_names})"
    )
    return chosen


def get_cached_task(
    task_name: str,
    task_digest: str,
    *,
    cache_root: Path = DEFAULT_CACHE_DIR,
) -> Path | None:
    """Return the cached task directory if it exists, otherwise None."""
    return _resolve_cached_task_dir(task_name, task_digest, cache_root=cache_root)


async def get_or_download_task(
    presigned_url: str,
    task_name: str,
    task_digest: str,
    *,
    cache_root: Path = DEFAULT_CACHE_DIR,
) -> Path:
    """Return the path to a cached task directory, downloading if needed.

    The cache is content-addressed: ``~/.cache/ridges/tasks/{digest}/{task_name}/``.
    Downloads use atomic rename to prevent corruption from concurrent access.
    """
    cached_dir = _cache_dir_for_digest(task_digest, cache_root=cache_root)
    cached_task_dir = _resolve_cached_task_dir(
        task_name,
        task_digest,
        cache_root=cache_root,
    )
    if cached_task_dir is not None:
        logger.info(f"Task cache hit for {task_digest}")
        return cached_task_dir

    logger.info(f"Task cache miss for {task_digest}, downloading...")
    cache_root.mkdir(parents=True, exist_ok=True)

    tmp_dir = cache_root / f".tmp-{uuid4().hex}"
    try:
        tmp_dir.mkdir(parents=True)

        archive_path = tmp_dir / "task.tar.gz"
        async with httpx.AsyncClient() as client:
            async with client.stream("GET", presigned_url, follow_redirects=True, timeout=120) as response:
                response.raise_for_status()
                with open(archive_path, "wb") as f:
                    async for chunk in response.aiter_bytes():
                        f.write(chunk)

        extract_dir = tmp_dir / "extracted"
        extract_dir.mkdir()
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(path=extract_dir, filter="data")

        task_dir = _resolved_extracted_task_dir(extract_dir)

        actual_digest = compute_task_digest(task_dir)
        if actual_digest != task_digest:
            raise RuntimeError(f"Task archive digest mismatch: expected {task_digest}, got {actual_digest}")

        staged_digest_dir, _ = _stage_extracted_task_dir(
            extract_dir,
            source_task_dir=task_dir,
            task_name=task_name,
        )
        try:
            staged_digest_dir.rename(cached_dir)
        except OSError:
            if cached_dir.exists():
                logger.info(f"Concurrent download resolved for {task_digest}")
            else:
                raise

        cached_task_dir = _resolve_cached_task_dir(
            task_name,
            task_digest,
            cache_root=cache_root,
        )
        if cached_task_dir is None:
            raise RuntimeError(f"Cached digest directory {cached_dir} was created without a readable task directory")

        logger.info(f"Task cached at {cached_task_dir}")
    finally:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return cached_task_dir
