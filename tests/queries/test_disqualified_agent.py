from __future__ import annotations

from uuid import UUID, uuid4

import pytest

import utils.database as _db
from queries.agent import get_top_agents
from queries.disqualified_agent import disqualify_agent, get_disqualified_agent
from queries.evaluation_set import get_evaluation_set_leaderboard_agents


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db):
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE disqualified_agents, agent_scores, evaluation_sets, agents RESTART IDENTITY CASCADE"
        )
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE disqualified_agents, agent_scores, evaluation_sets, agents RESTART IDENTITY CASCADE"
        )


async def _insert_agent() -> UUID:
    agent_id = uuid4()
    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO agents (
                agent_id, miner_hotkey, miner_coldkey, name, version_num,
                status, created_at, ip_address
            )
            VALUES ($1, $2, $3, 'test-agent', 0, 'evaluating', NOW(), '127.0.0.1')
            """,
            agent_id,
            f"hotkey-{agent_id}",
            f"coldkey-{agent_id}",
        )
    return agent_id


@pytest.mark.anyio
async def test_get_returns_none_when_not_disqualified() -> None:
    agent_id = await _insert_agent()
    assert await get_disqualified_agent(agent_id) is None


@pytest.mark.anyio
async def test_disqualify_inserts_and_get_roundtrips() -> None:
    agent_id = await _insert_agent()

    result = await disqualify_agent(agent_id, "cheating")

    assert result.agent_id == agent_id
    assert result.reason == "cheating"
    assert result.disqualified_at is not None

    fetched = await get_disqualified_agent(agent_id)
    assert fetched is not None
    assert fetched.agent_id == agent_id
    assert fetched.reason == "cheating"


@pytest.mark.anyio
async def test_disqualify_is_idempotent_and_updates_reason() -> None:
    agent_id = await _insert_agent()

    await disqualify_agent(agent_id, "first reason")
    second = await disqualify_agent(agent_id, "second reason")

    assert second.reason == "second reason"

    async with _db.pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM disqualified_agents WHERE agent_id = $1", agent_id)
    assert count == 1


async def _insert_scored_agent(*, final_score: float, coldkey: str) -> UUID:
    agent_id = uuid4()
    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO agents (
                agent_id, miner_hotkey, miner_coldkey, name, version_num,
                status, created_at, ip_address
            )
            VALUES ($1, $2, $3, 'scored-agent', 0, 'finished', NOW(), '127.0.0.1')
            """,
            agent_id,
            f"hotkey-{agent_id}",
            coldkey,
        )
        max_set_id = await conn.fetchval("SELECT MAX(set_id) FROM evaluation_sets")
        if max_set_id is None:
            max_set_id = 1
            await conn.execute(
                """
                INSERT INTO evaluation_sets (set_id, set_group, problem_name)
                VALUES ($1, 'validator', 'p1')
                ON CONFLICT (set_id, set_group, problem_name) DO NOTHING
                """,
                max_set_id,
            )
        await conn.execute(
            """
            INSERT INTO agent_scores (
                agent_id, miner_hotkey, name, version_num, created_at, status,
                set_id, approved, approved_at, validator_count, final_score
            )
            VALUES ($1, $2, 'scored-agent', 0, NOW(), 'finished',
                    $3, TRUE, NOW(), 3, $4)
            """,
            agent_id,
            f"hotkey-{agent_id}",
            max_set_id,
            final_score,
        )
    return agent_id


@pytest.mark.anyio
async def test_disqualified_agent_excluded_from_top_agents() -> None:
    kept = await _insert_scored_agent(final_score=0.9, coldkey="ck-kept")
    stopped = await _insert_scored_agent(final_score=0.8, coldkey="ck-stopped")

    before = {agent.agent_id for agent in await get_top_agents(number_of_agents=10)}
    assert kept in before
    assert stopped in before

    await disqualify_agent(stopped, "cheating")

    after = {agent.agent_id for agent in await get_top_agents(number_of_agents=10)}
    assert kept in after
    assert stopped not in after


@pytest.mark.anyio
async def test_stopped_agent_marked_disqualified_on_leaderboard() -> None:
    # Reuse the scored-agent helper; the agent must fall in the latest set window.
    stopped = await _insert_scored_agent(final_score=0.8, coldkey="ck-lb-stopped")
    max_set_id = None
    async with _db.pool.acquire() as conn:
        max_set_id = await conn.fetchval("SELECT MAX(set_id) FROM evaluation_sets")
        # Pin the agent to the set explicitly so it falls in the leaderboard's window
        # regardless of created_at ordering versus the set/competition row.
        await conn.execute("UPDATE agents SET set_id = $1 WHERE agent_id = $2", max_set_id, stopped)

    await disqualify_agent(stopped, "cheating")

    rows = await get_evaluation_set_leaderboard_agents(max_set_id)
    match = [r for r in rows if r["agent_id"] == stopped]
    assert match, "stopped agent should still appear on the leaderboard"
    assert match[0]["disqualified"] is True
