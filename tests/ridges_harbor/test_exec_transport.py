from pathlib import Path
from types import SimpleNamespace

import pytest
from kubernetes.client.rest import ApiException
from websocket import WebSocketConnectionClosedException

import ridges_harbor.k8s_environment as k8s_environment
from ridges_harbor.agents import RidgesMinerAgent
from ridges_harbor.k8s_environment import RidgesKubernetesEnvironment
from ridges_harbor.runtime_contract import ExecTransportError, MinerRuntimeError


class FakeResp:
    """Minimal stand-in for a kubernetes-client WSClient."""

    def __init__(self, returncode, stdout=""):
        self._returncode = returncode
        self._stdout = stdout

    def is_open(self):
        return bool(self._stdout)

    def update(self, timeout):
        pass

    def peek_stdout(self):
        return bool(self._stdout)

    def read_stdout(self):
        out, self._stdout = self._stdout, ""
        return out

    def peek_stderr(self):
        return False

    def read_stderr(self):
        return ""

    def run_forever(self, timeout):
        pass

    @property
    def returncode(self):
        return self._returncode

    def close(self):
        pass


class _Api:
    connect_get_namespaced_pod_exec = object()


def make_env() -> RidgesKubernetesEnvironment:
    env = RidgesKubernetesEnvironment.__new__(RidgesKubernetesEnvironment)
    env.pod_name = "test-pod"
    env.namespace = "test-ns"
    env._core_api = _Api()  # backs the read-only `_api` property
    env._resolve_user = lambda user: None
    env._merge_env = lambda extra: {}
    env.task_env_config = SimpleNamespace(workdir=None)

    async def _noop():
        pass

    env._ensure_client = _noop
    return env


@pytest.mark.anyio
async def test_websocket_close_raises_exec_transport_error(monkeypatch) -> None:
    def dead_stream(*args, **kwargs):
        raise WebSocketConnectionClosedException("Connection to remote host was lost.")

    monkeypatch.setattr(k8s_environment, "stream", dead_stream)

    with pytest.raises(ExecTransportError, match="died"):
        await make_env().exec("echo hi")


@pytest.mark.anyio
async def test_websocket_close_during_read_raises_exec_transport_error(monkeypatch) -> None:
    """A stream that dies mid-read (inside the real _read_exec_output loop)."""

    class DyingResp(FakeResp):
        def __init__(self):
            super().__init__(returncode=None, stdout="partial")

        def update(self, timeout):
            raise WebSocketConnectionClosedException("Connection to remote host was lost.")

    monkeypatch.setattr(k8s_environment, "stream", lambda *a, **k: DyingResp())

    with pytest.raises(ExecTransportError, match="died"):
        await make_env().exec("echo hi")


@pytest.mark.anyio
async def test_missing_exit_status_raises_exec_transport_error(monkeypatch) -> None:
    monkeypatch.setattr(k8s_environment, "stream", lambda *a, **k: FakeResp(returncode=None))

    with pytest.raises(ExecTransportError, match="without an exit status"):
        await make_env().exec("echo hi")


@pytest.mark.anyio
async def test_malformed_exit_status_raises_exec_transport_error(monkeypatch) -> None:
    """kubernetes WSClient.returncode raises TypeError when the status channel is empty."""

    class MalformedResp(FakeResp):
        def __init__(self):
            super().__init__(returncode=None)

        @property
        def returncode(self):
            raise TypeError("'NoneType' object is not subscriptable")

    monkeypatch.setattr(k8s_environment, "stream", lambda *a, **k: MalformedResp())

    with pytest.raises(ExecTransportError, match="malformed exit status"):
        await make_env().exec("echo hi")


@pytest.mark.anyio
async def test_upgrade_failure_status_zero_raises_exec_transport_error(monkeypatch) -> None:
    def failing_stream(*args, **kwargs):
        raise ApiException(status=0, reason="websocket handshake failed")

    monkeypatch.setattr(k8s_environment, "stream", failing_stream)

    with pytest.raises(ExecTransportError, match="failed to establish"):
        await make_env().exec("echo hi")


@pytest.mark.anyio
async def test_successful_exec_still_returns_result(monkeypatch) -> None:
    monkeypatch.setattr(k8s_environment, "stream", lambda *a, **k: FakeResp(returncode=0, stdout="hi\n"))

    result = await make_env().exec("echo hi")

    assert result.return_code == 0
    assert result.stdout == "hi\n"


@pytest.mark.anyio
async def test_exec_defaults_to_task_environment_workdir(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def successful_stream(*args, **kwargs):
        captured["command"] = kwargs["command"]
        return FakeResp(returncode=0)

    environment = make_env()
    environment.task_env_config = SimpleNamespace(workdir="/task-workdir")
    monkeypatch.setattr(k8s_environment, "stream", successful_stream)

    await environment.exec("pwd")

    assert captured["command"] == ["sh", "-c", "cd /task-workdir && bash -c pwd"]


@pytest.mark.anyio
async def test_nonzero_exit_still_returns_exec_result(monkeypatch) -> None:
    """A genuine agent exit code delivered via the status channel keeps the old path."""
    monkeypatch.setattr(k8s_environment, "stream", lambda *a, **k: FakeResp(returncode=137))

    result = await make_env().exec("run the miner")

    assert result.return_code == 137


@pytest.mark.anyio
async def test_statusful_api_error_still_returns_exec_result(monkeypatch) -> None:
    def failing_stream(*args, **kwargs):
        raise ApiException(status=500, reason="internal error")

    monkeypatch.setattr(k8s_environment, "stream", failing_stream)

    result = await make_env().exec("echo hi")

    assert result.return_code == 1
    assert "API error (500)" in result.stderr


def make_agent(tmp_path: Path) -> RidgesMinerAgent:
    agent = RidgesMinerAgent.__new__(RidgesMinerAgent)
    agent.logs_dir = tmp_path
    return agent


@pytest.mark.anyio
async def test_exec_with_log_propagates_transport_error_unwrapped(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)

    async def executor(environment, command, cwd=None):
        raise ExecTransportError("Exec stream to Pod test-pod died")

    with pytest.raises(ExecTransportError):
        await agent._exec_with_log(
            environment=None,
            executor=executor,
            command="run the miner",
            log_filename="run.log",
            cancelled_detail="cancelled",
            error_summary="Miner runtime failed",
            error_type=MinerRuntimeError,
        )

    assert "Exec stream to Pod test-pod died" in (tmp_path / "run.log").read_text()


@pytest.mark.anyio
async def test_exec_with_log_still_wraps_other_errors(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)

    async def executor(environment, command, cwd=None):
        raise ValueError("agent blew up")

    with pytest.raises(MinerRuntimeError, match="Miner runtime failed"):
        await agent._exec_with_log(
            environment=None,
            executor=executor,
            command="run the miner",
            log_filename="run.log",
            cancelled_detail="cancelled",
            error_summary="Miner runtime failed",
            error_type=MinerRuntimeError,
        )
