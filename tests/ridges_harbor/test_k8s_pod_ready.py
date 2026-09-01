from unittest.mock import MagicMock

import pytest

from ridges_harbor.k8s_environment import KubernetesEnvironment


def _status(*, name: str, waiting=None, terminated=None, last_terminated=None, ready: bool = False):
    status = MagicMock()
    status.name = name
    status.ready = ready
    status.state = MagicMock(waiting=waiting, terminated=terminated)
    status.last_state = MagicMock(terminated=last_terminated)
    return status


def _make_env() -> KubernetesEnvironment:
    env = KubernetesEnvironment.__new__(KubernetesEnvironment)
    env.pod_name = "test-pod"
    env.namespace = "ridges"
    env.logger = MagicMock()
    env._core_api = MagicMock()
    return env


def _pod(*, phase: str, containers=None, inits=None):
    pod = MagicMock()
    pod.status = MagicMock(
        phase=phase,
        reason=None,
        message=None,
        container_statuses=containers or [],
        init_container_statuses=inits or [],
    )
    return pod


@pytest.mark.anyio
async def test_init_image_pull_failure_fails_fast() -> None:
    waiting = MagicMock(reason="ImagePullBackOff", message="denied")
    env = _make_env()
    env._core_api.read_namespaced_pod.return_value = _pod(
        phase="Pending",
        inits=[_status(name="iptables-init", waiting=waiting)],
    )

    with pytest.raises(RuntimeError, match="init container 'iptables-init'.*denied"):
        await env._wait_for_pod_ready(timeout_sec=1)


@pytest.mark.anyio
async def test_init_crashloop_fails_fast() -> None:
    waiting = MagicMock(reason="CrashLoopBackOff", message="back-off")
    env = _make_env()
    env._core_api.read_namespaced_pod.return_value = _pod(
        phase="Pending",
        inits=[_status(name="iptables-init", waiting=waiting)],
    )

    with pytest.raises(RuntimeError, match="iptables-init.*back-off"):
        await env._wait_for_pod_ready(timeout_sec=1)


@pytest.mark.anyio
async def test_init_nonzero_exit_fails_fast() -> None:
    terminated = MagicMock(exit_code=2, reason="Error")
    env = _make_env()
    env._core_api.read_namespaced_pod.return_value = _pod(
        phase="Pending",
        inits=[_status(name="iptables-init", terminated=terminated)],
    )

    with pytest.raises(RuntimeError, match="exited with code 2"):
        await env._wait_for_pod_ready(timeout_sec=1)


@pytest.mark.anyio
async def test_successful_init_does_not_block_exec_ready() -> None:
    terminated = MagicMock(exit_code=0, reason="Completed")
    env = _make_env()
    env._core_api.read_namespaced_pod.return_value = _pod(
        phase="Running",
        containers=[_status(name="main", ready=True), _status(name="proxy", ready=True)],
        inits=[_status(name="iptables-init", terminated=terminated)],
    )

    await env._check_regular_containers_execable()


@pytest.mark.anyio
async def test_terminated_main_blocks_exec_ready() -> None:
    terminated = MagicMock(exit_code=137, reason="OOMKilled")
    env = _make_env()
    env._core_api.read_namespaced_pod.return_value = _pod(
        phase="Running",
        containers=[_status(name="main", terminated=terminated)],
        inits=[_status(name="iptables-init", terminated=MagicMock(exit_code=0, reason="Completed"))],
    )

    with pytest.raises(RuntimeError, match="Container 'main'.*exit_code=137"):
        await env._check_regular_containers_execable()


def test_failure_summary_includes_init_container() -> None:
    waiting = MagicMock(reason="CrashLoopBackOff")
    env = _make_env()
    pod = _pod(
        phase="Pending",
        inits=[_status(name="iptables-init", waiting=waiting)],
    )

    summary = env._pod_failure_summary(pod)

    assert "init container iptables-init waiting: CrashLoopBackOff" in summary
