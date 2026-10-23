from __future__ import annotations

import pytest

import utils.database as db
from models.agent import AgentStatus
from queries.agent import EvaluationCandidate, get_evaluation_candidates_for_validator_hotkey
from queries.evaluation import create_new_evaluation_and_evaluation_runs
from tests.queries.test_competition_lifecycle_gates import _insert_agent, _seed_competition

pytestmark = pytest.mark.anyio

# A platform validator hotkey that never logs in, so it stands in for a validator that has evaluated nothing.
PLATFORM_VALIDATOR_PROBE_HOTKEY = "platform-validator-probe"


@pytest.fixture(autouse=True)
async def clean(postgres_db):
    async with db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE competitions, agents, evaluation_sets, evaluations, evaluation_runs RESTART IDENTITY CASCADE"
        )
        await conn.execute("UPDATE competition_work_cursors SET last_served_set_id = NULL")


async def seed_agent(required_validator_count: int):
    async with db.pool.acquire() as conn:
        await _seed_competition(conn, set_id=29)
        await conn.execute(
            "UPDATE competitions SET required_validator_count = $1 WHERE set_id = 29", required_validator_count
        )
        return await _insert_agent(conn, set_id=29, status=AgentStatus.evaluating)


async def offered(hotkey):
    return [c.agent_id for c in (await get_evaluation_candidates_for_validator_hotkey(hotkey)).candidates]


async def claim(agent_id, hotkey):
    # Claim directly, bypassing the candidate query, as a racing request would.
    async with db.pool.acquire() as conn:
        cursor = await conn.fetchval(
            "SELECT last_served_set_id FROM competition_work_cursors WHERE family = 'validator'"
        )
    return await create_new_evaluation_and_evaluation_runs(EvaluationCandidate(agent_id, 29), hotkey, cursor)


async def test_platform_validators_take_at_most_one_of_three_slots():
    agent = await seed_agent(required_validator_count=3)
    assert await offered(PLATFORM_VALIDATOR_PROBE_HOTKEY) == [agent]
    assert await offered("platform-validator-1") == [agent]
    assert await claim(agent, "platform-validator-1") is not None

    assert await offered(PLATFORM_VALIDATOR_PROBE_HOTKEY) == []
    assert await offered("platform-validator-2") == []
    assert await claim(agent, "platform-validator-2") is None

    assert await offered("validator-a") == [agent]
    assert await claim(agent, "validator-a") is not None
    assert await claim(agent, "validator-b") is not None
    assert await offered("validator-c") == []


@pytest.mark.parametrize(("required", "platform_validator_slots"), [(2, 0), (3, 1), (4, 1), (5, 2)])
async def test_external_validators_always_keep_a_majority(required, platform_validator_slots):
    agent = await seed_agent(required_validator_count=required)
    for num in range(1, platform_validator_slots + 1):
        assert await claim(agent, f"platform-validator-{num}") is not None

    next_platform_validator = f"platform-validator-{platform_validator_slots + 1}"
    assert await offered(next_platform_validator) == []
    assert await claim(agent, next_platform_validator) is None
    assert await offered("validator-a") == [agent]
