from datetime import datetime, timezone
from uuid import uuid4

import pytest

import utils.database as _db
from queries.agent import get_agent_score_and_set_id

SET_CREATED = datetime(2026, 5, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db):
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE evaluation_sets, agents, agent_scores, benchmark_agent_ids RESTART IDENTITY CASCADE"
        )


async def _insert_eval_set(conn, set_id: int) -> None:
    await conn.execute(
        "INSERT INTO evaluation_sets (set_id, set_group, problem_name, created_at) VALUES ($1, $2, $3, $4)",
        set_id,
        "validator",
        "problem-a",
        SET_CREATED,
    )


async def _insert_agent(conn, *, agent_id) -> None:
    await conn.execute(
        """INSERT INTO agents (agent_id, miner_hotkey, name, version_num, status, created_at, ip_address)
           VALUES ($1, $2, $3, $4, $5, NOW(), $6)""",
        agent_id,
        f"hotkey-{agent_id}",
        f"agent-{agent_id}",
        1,
        "finished",
        "127.0.0.1",
    )


async def _insert_agent_score(conn, *, agent_id, set_id: int, final_score: float) -> None:
    await conn.execute(
        """INSERT INTO agent_scores
               (agent_id, miner_hotkey, name, version_num, created_at, status, set_id, approved, validator_count, final_score)
           VALUES ($1, $2, $3, $4, NOW(), $5, $6, $7, $8, $9)""",
        agent_id,
        f"hotkey-{agent_id}",
        f"agent-{agent_id}",
        1,
        "finished",
        set_id,
        False,
        1,
        final_score,
    )


@pytest.mark.anyio
async def test_get_agent_score_and_set_id_returns_agents_own_set():
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn, set_id=1)
        await _insert_eval_set(conn, set_id=2)
        agent_id = uuid4()
        await _insert_agent(conn, agent_id=agent_id)
        # Agent's score lives in the OLDER set, not the latest.
        await _insert_agent_score(conn, agent_id=agent_id, set_id=1, final_score=0.842123456)

    result = await get_agent_score_and_set_id(agent_id)
    assert result == (1, 0.842123)


@pytest.mark.anyio
async def test_get_agent_score_and_set_id_returns_none_for_unscored_agent():
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn, set_id=1)
        agent_id = uuid4()
        await _insert_agent(conn, agent_id=agent_id)

    result = await get_agent_score_and_set_id(agent_id)
    assert result is None


@pytest.mark.anyio
async def test_get_agent_score_and_set_id_excludes_benchmark_agents():
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn, set_id=1)
        agent_id = uuid4()
        await _insert_agent(conn, agent_id=agent_id)
        await _insert_agent_score(conn, agent_id=agent_id, set_id=1, final_score=0.9)
        await conn.execute(
            "INSERT INTO benchmark_agent_ids (agent_id, description) VALUES ($1, 'benchmark')",
            agent_id,
        )

    result = await get_agent_score_and_set_id(agent_id)
    assert result is None
