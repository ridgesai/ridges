from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4
from weakref import WeakValueDictionary

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import api.config as config
from api.endpoints import admin as admin_endpoint
from api.endpoints import validator as validator_endpoint
from api.endpoints.admin import router as admin_router


def _make_validator(*, session_id, hotkey: str, ip_address: str = "127.0.0.1", evaluation_id=None):
    return validator_endpoint.Validator(
        session_id=session_id,
        name="TAOApp",
        hotkey=hotkey,
        time_connected=datetime.now(timezone.utc),
        ip_address=ip_address,
        current_evaluation_id=evaluation_id,
    )


def _fake_cleanup(monkeypatch) -> list[tuple]:
    cleanup_calls: list[tuple] = []

    async def fake_update_unfinished_evaluation_runs_in_evaluation_id_to_errored(_evaluation_id, reason: str) -> None:
        cleanup_calls.append(("mark_errored", _evaluation_id, reason))

    async def fake_handle_evaluation_if_finished(_evaluation_id) -> None:
        cleanup_calls.append(("handle_finished", _evaluation_id))

    monkeypatch.setattr(
        validator_endpoint,
        "update_unfinished_evaluation_runs_in_evaluation_id_to_errored",
        fake_update_unfinished_evaluation_runs_in_evaluation_id_to_errored,
    )
    monkeypatch.setattr(validator_endpoint, "handle_evaluation_if_finished", fake_handle_evaluation_if_finished)
    return cleanup_calls


@pytest.mark.anyio
async def test_kick_removes_session_and_errors_active_evaluation(monkeypatch) -> None:
    session_id = uuid4()
    evaluation_id = uuid4()
    validator = _make_validator(session_id=session_id, hotkey="validator-hotkey", evaluation_id=evaluation_id)
    monkeypatch.setattr(validator_endpoint, "SESSION_ID_TO_VALIDATOR", {session_id: validator})
    cleanup_calls = _fake_cleanup(monkeypatch)

    response = await admin_endpoint.delete_validator_session("validator-hotkey")

    assert response.status_code == 204
    assert session_id not in validator_endpoint.SESSION_ID_TO_VALIDATOR
    assert cleanup_calls == [
        ("mark_errored", evaluation_id, "The validator was kicked by an admin to force a restart."),
        ("handle_finished", evaluation_id),
    ]


@pytest.mark.anyio
async def test_kick_validator_without_evaluation_removes_session(monkeypatch) -> None:
    session_id = uuid4()
    validator = _make_validator(session_id=session_id, hotkey="validator-hotkey")
    monkeypatch.setattr(validator_endpoint, "SESSION_ID_TO_VALIDATOR", {session_id: validator})
    cleanup_calls = _fake_cleanup(monkeypatch)

    response = await admin_endpoint.delete_validator_session("validator-hotkey")

    assert response.status_code == 204
    assert validator_endpoint.SESSION_ID_TO_VALIDATOR == {}
    assert cleanup_calls == []


@pytest.mark.anyio
async def test_kick_waits_for_in_flight_registration(monkeypatch) -> None:
    monkeypatch.setattr(validator_endpoint, "SESSION_REGISTRATION_LOCKS", WeakValueDictionary())
    session_id = uuid4()
    validator = _make_validator(session_id=session_id, hotkey="validator-hotkey")
    monkeypatch.setattr(validator_endpoint, "SESSION_ID_TO_VALIDATOR", {session_id: validator})
    _fake_cleanup(monkeypatch)

    registration_lock = validator_endpoint.get_session_registration_lock("validator-hotkey")
    async with registration_lock:
        kick_task = asyncio.create_task(admin_endpoint.delete_validator_session("validator-hotkey"))
        await asyncio.sleep(0.05)
        assert not kick_task.done()

    response = await asyncio.wait_for(kick_task, timeout=1)
    assert response.status_code == 204
    assert session_id not in validator_endpoint.SESSION_ID_TO_VALIDATOR


@pytest.mark.anyio
async def test_kick_unknown_hotkey_returns_404(monkeypatch) -> None:
    monkeypatch.setattr(validator_endpoint, "SESSION_ID_TO_VALIDATOR", {})

    with pytest.raises(HTTPException) as exc_info:
        await admin_endpoint.delete_validator_session("missing-hotkey")

    assert exc_info.value.status_code == 404


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(admin_router, prefix="/admin")
    return app


def test_kick_route_requires_admin_bearer_key(monkeypatch) -> None:
    session_id = uuid4()
    validator = _make_validator(session_id=session_id, hotkey="validator-hotkey")
    monkeypatch.setattr(validator_endpoint, "SESSION_ID_TO_VALIDATOR", {session_id: validator})
    _fake_cleanup(monkeypatch)
    client = TestClient(_make_app())

    assert client.delete("/admin/validator-sessions/validator-hotkey").status_code == 401
    assert (
        client.delete(
            "/admin/validator-sessions/validator-hotkey",
            headers={"Authorization": "Bearer wrong-key"},
        ).status_code
        == 401
    )
    response = client.delete(
        "/admin/validator-sessions/validator-hotkey",
        headers={"Authorization": f"Bearer {config.COLDKEY_BAN_ADMIN_API_KEY}"},
    )
    assert response.status_code == 204
    assert session_id not in validator_endpoint.SESSION_ID_TO_VALIDATOR
