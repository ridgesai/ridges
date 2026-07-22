from __future__ import annotations

import asyncio
import gc
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
from weakref import WeakValueDictionary, ref

import pytest
from fastapi import HTTPException

from api.endpoints import validator as validator_endpoint
from api.endpoints.validator_models import ScreenerRegistrationRequest, ValidatorRegistrationRequest


@pytest.fixture(autouse=True)
def _fresh_session_registration_locks(monkeypatch) -> None:
    monkeypatch.setattr(validator_endpoint, "SESSION_REGISTRATION_LOCKS", WeakValueDictionary())


def test_unused_session_registration_lock_is_released() -> None:
    validator_hotkey = "screener-1-99"
    lock = validator_endpoint.get_session_registration_lock(validator_hotkey)
    lock_reference = ref(lock)

    assert validator_hotkey in validator_endpoint.SESSION_REGISTRATION_LOCKS

    del lock
    gc.collect()

    assert lock_reference() is None
    assert validator_hotkey not in validator_endpoint.SESSION_REGISTRATION_LOCKS


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

    assert validator_endpoint.SESSION_ID_TO_VALIDATOR == {old_session_id: old_validator}


@pytest.mark.anyio
async def test_screener_reregistration_cleans_up_active_evaluation(monkeypatch) -> None:
    old_session_id = uuid4()
    evaluation_id = uuid4()
    old_screener = _make_validator(
        session_id=old_session_id,
        hotkey="screener-1-1",
        ip_address="127.0.0.1",
        evaluation_id=evaluation_id,
    )
    monkeypatch.setattr(validator_endpoint, "SESSION_ID_TO_VALIDATOR", {old_session_id: old_screener})
    monkeypatch.setattr(validator_endpoint.config, "SCREENER_PASSWORD", "test-password")
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

    response = await validator_endpoint.validator_register_as_screener(
        _make_request("127.0.0.2"),
        ScreenerRegistrationRequest(
            name="screener-1-1",
            password="test-password",
            commit_hash="test-commit",
        ),
    )

    assert cleanup_calls == [
        ("mark_errored", evaluation_id, "Screener re-registered; replacing the old session."),
        ("handle_finished", evaluation_id),
    ]
    assert old_screener.current_evaluation_id is None
    assert response.session_id != old_session_id
    assert old_session_id not in validator_endpoint.SESSION_ID_TO_VALIDATOR
    assert response.session_id in validator_endpoint.SESSION_ID_TO_VALIDATOR


@pytest.mark.anyio
async def test_screener_registration_without_client_ip_returns_400(monkeypatch) -> None:
    monkeypatch.setattr(validator_endpoint, "SESSION_ID_TO_VALIDATOR", {})
    monkeypatch.setattr(validator_endpoint.config, "SCREENER_PASSWORD", "test-password")
    monkeypatch.setattr(validator_endpoint, "COMMIT_HASH", "test-commit")

    with pytest.raises(HTTPException) as exc_info:
        await validator_endpoint.validator_register_as_screener(
            SimpleNamespace(client=None),
            ScreenerRegistrationRequest(
                name="screener-1-1",
                password="test-password",
                commit_hash="test-commit",
            ),
        )

    assert exc_info.value.status_code == 400
    assert validator_endpoint.SESSION_ID_TO_VALIDATOR == {}


@pytest.mark.anyio
async def test_concurrent_screener_reregistrations_leave_one_session(monkeypatch) -> None:
    old_session_id = uuid4()
    evaluation_id = uuid4()
    old_screener = _make_validator(
        session_id=old_session_id,
        hotkey="screener-1-1",
        ip_address="127.0.0.1",
        evaluation_id=evaluation_id,
    )
    monkeypatch.setattr(validator_endpoint, "SESSION_ID_TO_VALIDATOR", {old_session_id: old_screener})
    monkeypatch.setattr(validator_endpoint.config, "SCREENER_PASSWORD", "test-password")
    monkeypatch.setattr(validator_endpoint, "COMMIT_HASH", "test-commit")

    cleanup_started = asyncio.Event()
    allow_cleanup_to_finish = asyncio.Event()

    async def blocking_cleanup(_evaluation_id, _reason: str) -> None:
        cleanup_started.set()
        await allow_cleanup_to_finish.wait()

    async def fake_handle_evaluation_if_finished(_evaluation_id) -> None:
        return None

    monkeypatch.setattr(
        validator_endpoint,
        "update_unfinished_evaluation_runs_in_evaluation_id_to_errored",
        blocking_cleanup,
    )
    monkeypatch.setattr(validator_endpoint, "handle_evaluation_if_finished", fake_handle_evaluation_if_finished)

    registration_request = ScreenerRegistrationRequest(
        name="screener-1-1",
        password="test-password",
        commit_hash="test-commit",
    )
    first_registration = asyncio.create_task(
        validator_endpoint.validator_register_as_screener(
            _make_request("127.0.0.2"),
            registration_request,
        )
    )
    await asyncio.wait_for(cleanup_started.wait(), timeout=1)

    second_registration = asyncio.create_task(
        validator_endpoint.validator_register_as_screener(
            _make_request("127.0.0.3"),
            registration_request,
        )
    )
    await asyncio.sleep(0)
    allow_cleanup_to_finish.set()

    first_response, second_response = await asyncio.wait_for(
        asyncio.gather(first_registration, second_registration),
        timeout=1,
    )

    matching_sessions = {
        session_id: validator
        for session_id, validator in validator_endpoint.SESSION_ID_TO_VALIDATOR.items()
        if validator.hotkey == registration_request.name
    }
    assert list(matching_sessions) == [second_response.session_id]
    assert first_response.session_id not in validator_endpoint.SESSION_ID_TO_VALIDATOR


@pytest.mark.anyio
async def test_slow_screener_reregistration_does_not_block_different_screener(monkeypatch) -> None:
    old_session_id = uuid4()
    evaluation_id = uuid4()
    old_screener = _make_validator(
        session_id=old_session_id,
        hotkey="screener-1-1",
        ip_address="127.0.0.1",
        evaluation_id=evaluation_id,
    )
    monkeypatch.setattr(validator_endpoint, "SESSION_ID_TO_VALIDATOR", {old_session_id: old_screener})
    monkeypatch.setattr(validator_endpoint.config, "SCREENER_PASSWORD", "test-password")
    monkeypatch.setattr(validator_endpoint, "COMMIT_HASH", "test-commit")

    cleanup_started = asyncio.Event()
    allow_cleanup_to_finish = asyncio.Event()

    async def blocking_cleanup(_evaluation_id, _reason: str) -> None:
        cleanup_started.set()
        await allow_cleanup_to_finish.wait()

    async def fake_handle_evaluation_if_finished(_evaluation_id) -> None:
        return None

    monkeypatch.setattr(
        validator_endpoint,
        "update_unfinished_evaluation_runs_in_evaluation_id_to_errored",
        blocking_cleanup,
    )
    monkeypatch.setattr(validator_endpoint, "handle_evaluation_if_finished", fake_handle_evaluation_if_finished)

    blocked_registration = asyncio.create_task(
        validator_endpoint.validator_register_as_screener(
            _make_request("127.0.0.2"),
            ScreenerRegistrationRequest(
                name="screener-1-1",
                password="test-password",
                commit_hash="test-commit",
            ),
        )
    )
    await asyncio.wait_for(cleanup_started.wait(), timeout=1)

    independent_registration = asyncio.create_task(
        validator_endpoint.validator_register_as_screener(
            _make_request("127.0.0.3"),
            ScreenerRegistrationRequest(
                name="screener-1-2",
                password="test-password",
                commit_hash="test-commit",
            ),
        )
    )
    completed, _ = await asyncio.wait({independent_registration}, timeout=1)
    independent_completed_before_release = independent_registration in completed

    allow_cleanup_to_finish.set()
    blocked_response, independent_response = await asyncio.wait_for(
        asyncio.gather(blocked_registration, independent_registration),
        timeout=1,
    )

    assert independent_completed_before_release
    assert blocked_response.session_id in validator_endpoint.SESSION_ID_TO_VALIDATOR
    assert independent_response.session_id in validator_endpoint.SESSION_ID_TO_VALIDATOR
