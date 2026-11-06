from __future__ import annotations

from uuid import uuid4

import pytest

from api.endpoints import validator as validator_endpoint
from api.src.utils.openrouter_keys import OpenRouterKeyError

pytestmark = pytest.mark.anyio


@pytest.fixture
def finish(monkeypatch):
    """Stub the database side of finishing; record verifier key deletions."""
    state = {"deleted": [], "hydrated": 0, "delete_error": None}

    async def fake_finished_at(_evaluation_id):
        return None

    async def fake_hydrated(_evaluation_id):
        state["hydrated"] += 1
        return None

    async def fake_delete(key_hash):
        state["deleted"].append(key_hash)
        if state["delete_error"] is not None:
            raise state["delete_error"]

    monkeypatch.setattr(validator_endpoint, "update_evaluation_finished_at", fake_finished_at)
    monkeypatch.setattr(validator_endpoint, "get_hydrated_evaluation_by_id", fake_hydrated)
    monkeypatch.setattr(validator_endpoint, "delete_verifier_key", fake_delete)
    monkeypatch.setattr(validator_endpoint, "VERIFIER_KEY_HASH_BY_EVALUATION_ID", {})
    return state


async def test_finish_deletes_key_once(finish):
    evaluation_id = uuid4()
    validator_endpoint.VERIFIER_KEY_HASH_BY_EVALUATION_ID[evaluation_id] = "h1"

    await validator_endpoint.handle_evaluation_if_finished(evaluation_id)
    await validator_endpoint.handle_evaluation_if_finished(evaluation_id)

    assert finish["deleted"] == ["h1"]
    assert validator_endpoint.VERIFIER_KEY_HASH_BY_EVALUATION_ID == {}


async def test_finish_without_key_makes_no_call(finish):
    await validator_endpoint.handle_evaluation_if_finished(uuid4())

    assert finish["deleted"] == []
    assert finish["hydrated"] == 1


async def test_delete_failure_does_not_break_finish(finish):
    evaluation_id = uuid4()
    validator_endpoint.VERIFIER_KEY_HASH_BY_EVALUATION_ID[evaluation_id] = "h1"
    finish["delete_error"] = OpenRouterKeyError("Deleting verifier key failed")

    await validator_endpoint.handle_evaluation_if_finished(evaluation_id)

    assert finish["deleted"] == ["h1"]
    assert finish["hydrated"] == 1
