"""Tests for GET /retrieval/evaluations-for-agent caching."""

import asyncio
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

import api.endpoints.retrieval as retrieval_endpoint
from models.evaluation import Evaluation
from models.evaluation_set import EvaluationSetGroup
from utils.ttl import clear_all_ttl_caches

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def clear_caches():
    clear_all_ttl_caches()
    yield
    clear_all_ttl_caches()


def _evaluation(agent_id: UUID) -> Evaluation:
    return Evaluation(
        evaluation_id=uuid4(),
        agent_id=agent_id,
        validator_hotkey="5Fhotkey",
        set_id=29,
        evaluation_set_group=EvaluationSetGroup.validator,
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def loaded_agent_ids(monkeypatch) -> list[UUID]:
    loaded: list[UUID] = []

    async def fake_get_evaluations_for_agent_id(agent_id):
        loaded.append(agent_id)
        # Hold the load open so concurrent requests arrive while it is in flight.
        await asyncio.sleep(0.01)
        return [_evaluation(agent_id)]

    async def fake_get_evaluation_runs_for_public_view(evaluation_id):
        return []

    monkeypatch.setattr(retrieval_endpoint, "get_evaluations_for_agent_id", fake_get_evaluations_for_agent_id)
    monkeypatch.setattr(
        retrieval_endpoint,
        "get_evaluation_runs_for_public_view",
        fake_get_evaluation_runs_for_public_view,
    )
    return loaded


async def test_concurrent_requests_for_one_agent_load_once(loaded_agent_ids):
    agent_id = uuid4()

    responses = await asyncio.gather(*[retrieval_endpoint.evaluations_for_agent(agent_id) for _ in range(50)])

    assert loaded_agent_ids == [agent_id]
    assert all(response[0].agent_id == agent_id for response in responses)


async def test_agents_are_cached_separately(loaded_agent_ids):
    agent_a, agent_b = uuid4(), uuid4()

    response_a = await retrieval_endpoint.evaluations_for_agent(agent_a)
    response_b = await retrieval_endpoint.evaluations_for_agent(agent_b)
    repeat_a = await retrieval_endpoint.evaluations_for_agent(agent_a)

    assert loaded_agent_ids == [agent_a, agent_b]
    assert response_a[0].agent_id == agent_a
    assert response_b[0].agent_id == agent_b
    assert repeat_a[0].agent_id == agent_a
