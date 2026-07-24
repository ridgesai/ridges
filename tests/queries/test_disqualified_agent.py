from __future__ import annotations

from uuid import UUID, uuid4

import pytest

import utils.database as _db
from queries.disqualified_agent import disqualify_agent, get_disqualified_agent


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db):
    async with _db.pool.acquire() as conn:
        await conn.execute("TRUNCATE disqualified_agents, agents RESTART IDENTITY CASCADE")
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute("TRUNCATE disqualified_agents, agents RESTART IDENTITY CASCADE")


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
