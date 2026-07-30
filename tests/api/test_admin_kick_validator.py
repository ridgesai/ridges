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

