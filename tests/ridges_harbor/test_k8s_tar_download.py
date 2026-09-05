from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from tenacity import stop_after_attempt, wait_none

from ridges_harbor.k8s_environment import KubernetesEnvironment
from ridges_harbor.runtime_contract import ExecTransportError


class _FakeTarStream:
    def __init__(
        self,
        *,
        stdout: bytes | str | list[bytes | str] = b"",
        stderr: str = "",
        returncode: int | None = 0,
    ) -> None:
        if isinstance(stdout, list):
            self._stdout_chunks: list[bytes | str] = list(stdout)
        else:
            self._stdout_chunks = [stdout] if stdout else []
        self._stderr = stderr
        self._open = True
        self.returncode = returncode
        self.closed = False
        self.run_forever_calls = 0

    def is_open(self) -> bool:
        return self._open

    def update(self, timeout: float = 1) -> None:
        if not self._stdout_chunks:
            self._open = False

    def peek_stdout(self) -> bool:
        return bool(self._stdout_chunks)

    def read_stdout(self) -> bytes | str:
        return self._stdout_chunks.pop(0)

    def peek_stderr(self) -> bool:
        return bool(self._stderr)

    def read_stderr(self) -> str:
        value = self._stderr
        self._stderr = ""
        return value

    def run_forever(self, timeout: float = 0) -> None:
        self.run_forever_calls += 1
        self._open = False

    def close(self) -> None:
        self.closed = True


def _make_env() -> KubernetesEnvironment:
    env = KubernetesEnvironment.__new__(KubernetesEnvironment)
    env.pod_name = "test-pod"
    env.namespace = "ridges"
    env.logger = MagicMock()
    env._core_api = MagicMock()
    return env


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def no_download_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(KubernetesEnvironment.download_dir.retry, "wait", wait_none())
    monkeypatch.setattr(KubernetesEnvironment.download_dir.retry, "stop", stop_after_attempt(1))
    monkeypatch.setattr(KubernetesEnvironment.download_file.retry, "wait", wait_none())
    monkeypatch.setattr(KubernetesEnvironment.download_file.retry, "stop", stop_after_attempt(1))


def test_read_tar_stream_returns_data_and_closes() -> None:
    env = _make_env()
    resp = _FakeTarStream(stdout=b"ustar-bytes", returncode=0)

    tar_data, stderr = env._read_tar_stream(resp)

    assert tar_data == b"ustar-bytes"
    assert stderr == ""
    assert resp.run_forever_calls == 1
    assert resp.closed is True


def test_read_tar_stream_nonzero_returncode_includes_stderr() -> None:
    env = _make_env()
    resp = _FakeTarStream(stdout=b"", stderr="tar: cannot cd: No such file", returncode=2)

    with pytest.raises(RuntimeError, match="return_code=2.*cannot cd"):
        env._read_tar_stream(resp)
    assert resp.closed is True


def test_read_tar_stream_missing_returncode_is_transport_error() -> None:
    env = _make_env()
    resp = _FakeTarStream(stdout=b"", stderr="", returncode=None)

    with pytest.raises(ExecTransportError, match="closed without an exit status"):
        env._read_tar_stream(resp)
    assert resp.closed is True


@pytest.mark.anyio
async def test_download_dir_empty_data_includes_stderr(
    tmp_path, monkeypatch: pytest.MonkeyPatch, no_download_retry: None
) -> None:
    env = _make_env()

    async def _ready() -> None:
        return None

    async def _ensure() -> None:
        return None

    env._wait_for_container_exec_ready = _ready
    env._ensure_client = _ensure
    resp = _FakeTarStream(stdout=b"", stderr="stream dropped", returncode=0)
    monkeypatch.setattr("ridges_harbor.k8s_environment.stream", lambda *args, **kwargs: resp)

    with pytest.raises(RuntimeError, match="No data received.*/logs/verifier.*stream dropped"):
        await env.download_dir("/logs/verifier", tmp_path)
    assert resp.closed is True


@pytest.mark.anyio
async def test_download_dir_missing_directory(
    tmp_path, monkeypatch: pytest.MonkeyPatch, no_download_retry: None
) -> None:
    env = _make_env()

    async def _ready() -> None:
        return None

    async def _ensure() -> None:
        return None

    env._wait_for_container_exec_ready = _ready
    env._ensure_client = _ensure
    resp = _FakeTarStream(stdout=b"", stderr="sh: cd: /logs/verifier: No such file or directory", returncode=1)
    monkeypatch.setattr("ridges_harbor.k8s_environment.stream", lambda *args, **kwargs: resp)

    with pytest.raises(RuntimeError, match="Tar command failed.*No such file"):
        await env.download_dir("/logs/verifier", tmp_path)
    assert resp.closed is True


@pytest.mark.anyio
async def test_download_dir_waits_for_exec_ready(
    tmp_path, monkeypatch: pytest.MonkeyPatch, no_download_retry: None
) -> None:
    env = _make_env()
    waited = False

    async def _ready() -> None:
        nonlocal waited
        waited = True

    async def _ensure() -> None:
        return None

    env._wait_for_container_exec_ready = _ready
    env._ensure_client = _ensure
    resp = _FakeTarStream(stdout=b"not-a-real-tar", returncode=0)
    monkeypatch.setattr("ridges_harbor.k8s_environment.stream", lambda *args, **kwargs: resp)

    def _extract(_tar_data: bytes, _target_dir) -> None:
        return None

    env._extract_tar_all = _extract

    await env.download_dir("/logs/verifier", tmp_path)
    assert waited is True
    assert resp.closed is True


@pytest.mark.anyio
async def test_download_file_waits_for_exec_ready(
    tmp_path, monkeypatch: pytest.MonkeyPatch, no_download_retry: None
) -> None:
    env = _make_env()
    waited = False

    async def _ready() -> None:
        nonlocal waited
        waited = True

    async def _ensure() -> None:
        return None

    env._wait_for_container_exec_ready = _ready
    env._ensure_client = _ensure
    resp = _FakeTarStream(stdout=b"file-tar", returncode=0)
    monkeypatch.setattr("ridges_harbor.k8s_environment.stream", lambda *args, **kwargs: resp)

    def _extract(_tar_data: bytes, _source_path: str, _target_path) -> None:
        return None

    env._extract_tar_member = _extract

    await env.download_file("/logs/verifier/reward.txt", tmp_path / "reward.txt")
    assert waited is True
    assert resp.closed is True
