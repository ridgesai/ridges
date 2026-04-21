import io
import tarfile
from pathlib import Path

import pytest

import utils.task_cache as task_cache_module
from ridges_harbor.digest import compute_task_digest


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _task_archive_bytes(task_dir: Path, *, top_level_name: str) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        tar.add(task_dir, arcname=top_level_name)
    return buffer.getvalue()


def _loose_task_archive_bytes(task_dir: Path) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path in sorted(task_dir.rglob("*")):
            tar.add(path, arcname=path.relative_to(task_dir))
    return buffer.getvalue()


class _FakeStreamResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self) -> None:
        return None

    async def aiter_bytes(self):
        yield self._payload


class _FakeAsyncClient:
    def __init__(self, payload: bytes):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def stream(self, method: str, url: str, **kwargs):
        return _FakeStreamResponse(self._payload)


@pytest.mark.anyio
async def test_get_or_download_task_caches_under_digest_then_task_name(tmp_path: Path, monkeypatch) -> None:
    source_task_dir = tmp_path / "source-task"
    _write(source_task_dir / "instruction.md", "Solve the problem.\n")
    _write(source_task_dir / "task.toml", 'version = "1.0"\n')
    _write(source_task_dir / "tests" / "test.sh", "#!/bin/bash\n")
    digest = compute_task_digest(source_task_dir)
    archive_bytes = _task_archive_bytes(source_task_dir, top_level_name="downloaded-task")

    monkeypatch.setattr(
        task_cache_module.httpx,
        "AsyncClient",
        lambda: _FakeAsyncClient(archive_bytes),
    )

    cached_task_dir = await task_cache_module.get_or_download_task(
        "https://example.test/task.tar.gz",
        "update-status-file",
        digest,
        cache_root=tmp_path / "cache",
    )

    expected = tmp_path / "cache" / digest.replace(":", "_") / "update-status-file"
    assert cached_task_dir == expected
    assert (cached_task_dir / "instruction.md").exists()
    assert (cached_task_dir / "task.toml").exists()
    assert (cached_task_dir / "tests" / "test.sh").exists()


@pytest.mark.anyio
async def test_get_or_download_task_handles_loose_top_level_files(tmp_path: Path, monkeypatch) -> None:
    source_task_dir = tmp_path / "source-task"
    _write(source_task_dir / "instruction.md", "Solve the problem.\n")
    _write(source_task_dir / "task.toml", 'version = "1.0"\n')
    _write(source_task_dir / "tests" / "test.sh", "#!/bin/bash\n")
    digest = compute_task_digest(source_task_dir)
    archive_bytes = _loose_task_archive_bytes(source_task_dir)

    monkeypatch.setattr(
        task_cache_module.httpx,
        "AsyncClient",
        lambda: _FakeAsyncClient(archive_bytes),
    )

    cached_task_dir = await task_cache_module.get_or_download_task(
        "https://example.test/task.tar.gz",
        "update-status-file",
        digest,
        cache_root=tmp_path / "cache",
    )

    assert (cached_task_dir / "instruction.md").exists()
    assert (cached_task_dir / "task.toml").exists()
    assert (cached_task_dir / "tests" / "test.sh").exists()


@pytest.mark.anyio
async def test_get_cached_task_reuses_existing_digest_dir_for_different_task_name(
    tmp_path: Path,
) -> None:
    source_task_dir = tmp_path / "source-task"
    _write(source_task_dir / "instruction.md", "Solve the problem.\n")
    _write(source_task_dir / "task.toml", 'version = "1.0"\n')
    _write(source_task_dir / "tests" / "test.sh", "#!/bin/bash\n")
    digest = compute_task_digest(source_task_dir)
    cached_task_dir = tmp_path / "cache" / digest.replace(":", "_") / "first-name"
    _write(cached_task_dir / "instruction.md", "Solve the problem.\n")
    _write(cached_task_dir / "task.toml", 'version = "1.0"\n')
    _write(cached_task_dir / "tests" / "test.sh", "#!/bin/bash\n")

    assert (
        task_cache_module.get_cached_task(
            "first-name",
            digest,
            cache_root=tmp_path / "cache",
        )
        == cached_task_dir
    )
    assert (
        task_cache_module.get_cached_task(
            "second-name",
            digest,
            cache_root=tmp_path / "cache",
        )
        == cached_task_dir
    )


@pytest.mark.anyio
async def test_get_or_download_task_reuses_existing_digest_dir_for_different_task_name(
    tmp_path: Path, monkeypatch
) -> None:
    source_task_dir = tmp_path / "source-task"
    _write(source_task_dir / "instruction.md", "Solve the problem.\n")
    _write(source_task_dir / "task.toml", 'version = "1.0"\n')
    _write(source_task_dir / "tests" / "test.sh", "#!/bin/bash\n")
    digest = compute_task_digest(source_task_dir)
    cached_task_dir = tmp_path / "cache" / digest.replace(":", "_") / "first-name"
    _write(cached_task_dir / "instruction.md", "Solve the problem.\n")
    _write(cached_task_dir / "task.toml", 'version = "1.0"\n')
    _write(cached_task_dir / "tests" / "test.sh", "#!/bin/bash\n")

    class _FailAsyncClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("network should not be used when digest is already cached")

    monkeypatch.setattr(task_cache_module.httpx, "AsyncClient", _FailAsyncClient)

    resolved = await task_cache_module.get_or_download_task(
        "https://example.test/task.tar.gz",
        "second-name",
        digest,
        cache_root=tmp_path / "cache",
    )

    assert resolved == cached_task_dir


def test_compute_task_digest_changes_when_file_mode_changes(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    _write(task_dir / "instruction.md", "Solve the problem.\n")
    _write(task_dir / "task.toml", 'version = "1.0"\n')
    script_path = task_dir / "tests" / "test.sh"
    _write(script_path, "#!/bin/bash\n")

    script_path.chmod(0o644)
    non_executable_digest = compute_task_digest(task_dir)

    script_path.chmod(0o755)
    executable_digest = compute_task_digest(task_dir)

    assert executable_digest != non_executable_digest
