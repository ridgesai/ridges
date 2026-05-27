from __future__ import annotations

import time
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from api.endpoints import validator as validator_endpoint
from api.endpoints.validator_models import ValidatorRegistrationRequest


def _make_request(ip_address: str) -> SimpleNamespace:
    return SimpleNamespace(client=SimpleNamespace(host=ip_address))


def _make_validator(*, session_id, hotkey: str, ip_address: str, evaluation_id=None):
    return validator_endpoint.Validator(
        session_id=session_id,
        name="TAOApp",
        hotkey=hotkey,
        time_connected=datetime.now(timezone.utc),
        ip_address=ip_address,
        current_evaluation_id=evaluation_id,
    )


@pytest.mark.anyio
async def test_validator_reregistration_from_same_ip_cleans_up_active_evaluation(monkeypatch) -> None:
    old_session_id = uuid4()
    evaluation_id = uuid4()
    old_validator = _make_validator(
        session_id=old_session_id,
        hotkey="validator-hotkey",
        ip_address="127.0.0.1",
        evaluation_id=evaluation_id,
    )
    monkeypatch.setattr(validator_endpoint, "SESSION_ID_TO_VALIDATOR", {old_session_id: old_validator})
    monkeypatch.setattr(validator_endpoint, "validate_signed_timestamp", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(validator_endpoint, "is_validator_hotkey_whitelisted", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(validator_endpoint, "validator_hotkey_to_name", lambda *_args, **_kwargs: "TAOApp")
    monkeypatch.setattr(validator_endpoint, "COMMIT_HASH", "test-commit")

    cleanup_calls: list[tuple[str, object]] = []

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

    response = await validator_endpoint.validator_register_as_validator(
        _make_request("127.0.0.1"),
        ValidatorRegistrationRequest(
            timestamp=int(time.time()),
            signed_timestamp="signed",
            hotkey="validator-hotkey",
            commit_hash="test-commit",
        ),
    )

    assert cleanup_calls == [
        (
            "mark_errored",
            evaluation_id,
            "Validator re-registered from the same IP address; replacing the old session.",
        ),
        ("handle_finished", evaluation_id),
    ]
    assert old_validator.current_evaluation_id is None
    assert response.session_id != old_session_id
    assert old_session_id not in validator_endpoint.SESSION_ID_TO_VALIDATOR
    assert response.session_id in validator_endpoint.SESSION_ID_TO_VALIDATOR


@pytest.mark.anyio
async def test_validator_reregistration_from_different_ip_is_rejected(monkeypatch) -> None:
    old_session_id = uuid4()
    old_validator = _make_validator(
        session_id=old_session_id,
        hotkey="validator-hotkey",
        ip_address="127.0.0.1",
    )
    monkeypatch.setattr(validator_endpoint, "SESSION_ID_TO_VALIDATOR", {old_session_id: old_validator})
    monkeypatch.setattr(validator_endpoint, "validate_signed_timestamp", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(validator_endpoint, "is_validator_hotkey_whitelisted", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(validator_endpoint, "COMMIT_HASH", "test-commit")

    with pytest.raises(HTTPException, match="There is already a validator connected with the given hotkey."):
        await validator_endpoint.validator_register_as_validator(
            _make_request("127.0.0.2"),
            ValidatorRegistrationRequest(
                timestamp=int(time.time()),
                signed_timestamp="signed",
                hotkey="validator-hotkey",
                commit_hash="test-commit",
            ),
        )
