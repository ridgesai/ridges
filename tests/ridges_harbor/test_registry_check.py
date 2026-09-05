from __future__ import annotations

import http.client

import pytest
from tenacity import wait_none

import ridges_harbor.k8s_environment as k8s_environment_module
from ridges_harbor.k8s_environment import _RegistryUnavailable


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(k8s_environment_module._registry_manifest_present.retry, "wait", wait_none())


def _sequence(monkeypatch: pytest.MonkeyPatch, outcomes: list[object]) -> list[str]:
    """Feed HEAD outcomes in order; an exception instance is raised, an int is returned as the status."""
    calls: list[str] = []

    def fake_status(_registry: str, name: str, tag: str, **_kwargs: object) -> int:
        calls.append(f"{name}:{tag}")
        outcome = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
        if isinstance(outcome, BaseException):
            raise outcome
        return int(outcome)

    monkeypatch.setattr(k8s_environment_module, "_registry_manifest_status", fake_status)
    return calls


@pytest.mark.anyio
async def test_timeout_then_success_is_present(monkeypatch: pytest.MonkeyPatch, no_backoff: None) -> None:
    calls = _sequence(monkeypatch, [TimeoutError("timed out"), 200])

    assert await k8s_environment_module._registry_image_exists("registry:5000", "Task-Name", "abc-verifier") is True
    assert calls == ["task-name:abc-verifier"] * 2


@pytest.mark.anyio
async def test_404_is_final_without_retry(monkeypatch: pytest.MonkeyPatch, no_backoff: None) -> None:
    calls = _sequence(monkeypatch, [404, 200])

    assert await k8s_environment_module._registry_image_exists("registry:5000", "task", "abc-agent") is False
    assert len(calls) == 1


@pytest.mark.anyio
async def test_server_errors_and_protocol_errors_are_retried(monkeypatch: pytest.MonkeyPatch, no_backoff: None) -> None:
    calls = _sequence(monkeypatch, [503, 429, http.client.RemoteDisconnected("closed"), 200])

    assert await k8s_environment_module._registry_image_exists("registry:5000", "task", "abc-agent") is True
    assert len(calls) == 4


@pytest.mark.anyio
async def test_persistent_failure_raises_unavailable_after_all_attempts(
    monkeypatch: pytest.MonkeyPatch, no_backoff: None
) -> None:
    calls = _sequence(monkeypatch, [ConnectionRefusedError("down")])

    with pytest.raises(_RegistryUnavailable, match="task:abc-agent: ConnectionRefusedError: down"):
        await k8s_environment_module._registry_image_exists("registry:5000", "task", "abc-agent")
    assert len(calls) == k8s_environment_module.REGISTRY_CHECK_ATTEMPTS


@pytest.mark.anyio
async def test_other_client_error_is_final(monkeypatch: pytest.MonkeyPatch, no_backoff: None) -> None:
    calls = _sequence(monkeypatch, [401, 200])

    assert await k8s_environment_module._registry_image_exists("registry:5000", "task", "abc-agent") is False
    assert len(calls) == 1


@pytest.mark.anyio
async def test_unexpected_errors_are_treated_as_missing_without_retry(
    monkeypatch: pytest.MonkeyPatch, no_backoff: None
) -> None:
    calls = _sequence(monkeypatch, [TypeError("bug"), 200])

    assert await k8s_environment_module._registry_image_exists("registry:5000", "task", "abc-agent") is False
    assert len(calls) == 1
