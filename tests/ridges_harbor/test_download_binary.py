import io
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import ridges_harbor.k8s_environment as k8s_environment
from ridges_harbor.k8s_environment import RidgesKubernetesEnvironment

# Every byte value, repeated: guaranteed to contain invalid UTF-8 sequences.
BINARY = b"\x7fELF" + bytes(range(256)) * 64


class BinaryResp:
    """WSClient opened with binary=True: stdout and stderr arrive as bytes."""

    def __init__(self, stdout: bytes, stderr: bytes = b"", chunk: int = 1000):
        self._chunks = [stdout[i : i + chunk] for i in range(0, len(stdout), chunk)]
        self._stderr = stderr

    def is_open(self):
        return bool(self._chunks) or bool(self._stderr)

    def update(self, timeout):
        pass

    def peek_stdout(self):
        return bool(self._chunks)

    def read_stdout(self):
        return self._chunks.pop(0)

    def peek_stderr(self):
        return bool(self._stderr)

    def read_stderr(self):
        out, self._stderr = self._stderr, b""
        return out

    def run_forever(self, timeout):
        pass

    returncode = 0

    def close(self):
        pass


class _Api:
    connect_get_namespaced_pod_exec = object()


def make_env() -> RidgesKubernetesEnvironment:
    env = RidgesKubernetesEnvironment.__new__(RidgesKubernetesEnvironment)
    env.pod_name = "test-pod"
    env.namespace = "test-ns"
    env._core_api = _Api()  # backs the read-only `_api` property
    env.task_env_config = SimpleNamespace(workdir=None)

    async def _noop():
        pass

    env._ensure_client = _noop
    env._wait_for_container_exec_ready = _noop
    return env


def make_tar(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def patch_stream(monkeypatch, resp) -> list[dict]:
    calls: list[dict] = []

    def fake_stream(*args, **kwargs):
        calls.append(kwargs)
        return resp

    monkeypatch.setattr(k8s_environment, "stream", fake_stream)
    return calls


@pytest.mark.anyio
async def test_download_dir_keeps_binary_members_byte_exact(monkeypatch, tmp_path: Path) -> None:
    # Binary member first, reward.txt after it: with text decoding the offsets
    # shifted and tarfile stopped before reward.txt.
    archive = make_tar({"./go-output/focused.test": BINARY, "./reward.txt": b"1\n"})
    calls = patch_stream(monkeypatch, BinaryResp(archive))

    await make_env().download_dir("/logs/verifier", tmp_path)

    assert calls[0]["binary"] is True
    assert (tmp_path / "go-output" / "focused.test").read_bytes() == BINARY
    assert (tmp_path / "reward.txt").read_bytes() == b"1\n"


@pytest.mark.anyio
async def test_download_file_keeps_binary_byte_exact(monkeypatch, tmp_path: Path) -> None:
    archive = make_tar({"logs/verifier/focused.test": BINARY})
    calls = patch_stream(monkeypatch, BinaryResp(archive))

    await make_env().download_file("/logs/verifier/focused.test", tmp_path / "out.bin")

    assert calls[0]["binary"] is True
    assert (tmp_path / "out.bin").read_bytes() == BINARY


def test_read_tar_stream_decodes_bytes_stderr() -> None:
    resp = BinaryResp(b"", stderr=b"sh: 1: cd: can't cd to /logs/verifier\n")

    tar_data, stderr = make_env()._read_tar_stream(resp)

    assert tar_data == b""
    assert stderr == "sh: 1: cd: can't cd to /logs/verifier\n"
