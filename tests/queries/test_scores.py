from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

import utils.database as _db
from queries.scores import get_weight_receiving_agent_hotkey, get_weight_receiving_agent_info

SET_ID = 23
SET_CREATED_AT = datetime(2026, 6, 1, tzinfo=timezone.utc)
TRUNCATE_SCORE_TEST_TABLES = (
    "TRUNCATE evaluation_runs, evaluations, agent_scores, evaluation_sets, "
    "benchmark_agent_ids, agents RESTART IDENTITY CASCADE"
)

# TODO: Add more edge cases to scoring tests


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db):
    async with _db.pool.acquire() as conn:
        await conn.execute(TRUNCATE_SCORE_TEST_TABLES)
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute(TRUNCATE_SCORE_TEST_TABLES)


async def _insert_eval_set(conn) -> None:
    await conn.execute(
        """
        INSERT INTO evaluation_sets (set_id, set_group, problem_name, created_at)
        VALUES ($1, 'validator', 'problem-a', $2)
        """,
        SET_ID,
        SET_CREATED_AT,
    )


async def _insert_scored_agent(
    conn,
    *,
    miner_hotkey: str,
    final_score: float,
    cost_usd: float,
    approved: bool = True,
    approved_at: datetime | None = None,
    created_at: datetime,
) -> UUID:
    agent_id = uuid4()
    await conn.execute(
        """
        INSERT INTO agents (agent_id, miner_hotkey, name, version_num, status, created_at, ip_address)
        VALUES ($1, $2, $3, 0, 'finished', $4, '127.0.0.1')
        """,
        agent_id,
        miner_hotkey,
        miner_hotkey,
        created_at,
    )
    evaluation_id = uuid4()
    await conn.execute(
        """
        INSERT INTO evaluations (evaluation_id, agent_id, validator_hotkey, set_id, evaluation_set_group, created_at)
        VALUES ($1, $2, 'validator-hotkey', $3, 'validator', $4)
        """,
        evaluation_id,
        agent_id,
        SET_ID,
        created_at,
    )
    await conn.execute(
        """
        INSERT INTO evaluation_runs (
            evaluation_run_id, evaluation_id, problem_name, status, created_at,
            started_running_agent_at, finished_or_errored_at, verifier_reward, cost_usd
        )
        VALUES ($1, $2, 'problem-a', 'finished', $3, $3, $4, 1.0, $5)
        """,
        uuid4(),
        evaluation_id,
        created_at,
        created_at + timedelta(seconds=60),
        cost_usd,
    )
    if approved and approved_at is not None:
        await conn.execute(
            """
            INSERT INTO approved_agents (agent_id, set_id, approved_at)
            VALUES ($1, $2, $3)
            ON CONFLICT (agent_id, set_id) DO UPDATE
            SET approved_at = EXCLUDED.approved_at
            """,
            agent_id,
            SET_ID,
            approved_at,
        )
    await conn.execute(
        """
        INSERT INTO agent_scores (
            agent_id, miner_hotkey, name, version_num, created_at, status,
            set_id, approved, approved_at, validator_count, final_score
        )
        VALUES ($1, $2, $3, 0, $4, 'finished', $5, $6, $7, 1, $8)
        ON CONFLICT (agent_id) DO UPDATE
        SET
            miner_hotkey = EXCLUDED.miner_hotkey,
            name = EXCLUDED.name,
            version_num = EXCLUDED.version_num,
            created_at = EXCLUDED.created_at,
            status = EXCLUDED.status,
            set_id = EXCLUDED.set_id,
            approved = EXCLUDED.approved,
            approved_at = EXCLUDED.approved_at,
            validator_count = EXCLUDED.validator_count,
            final_score = EXCLUDED.final_score
        """,
        agent_id,
        miner_hotkey,
        miner_hotkey,
        created_at,
        SET_ID,
        approved,
        approved_at,
        final_score,
    )
    return agent_id


@pytest.mark.anyio
async def test_weight_receiver_is_top_scored_agent_when_eligible():
    now = datetime.now(timezone.utc)
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn)
        leader_id = await _insert_scored_agent(
            conn,
            miner_hotkey="leader-hotkey",
            final_score=0.50,
            cost_usd=0.10,
            approved_at=now - timedelta(hours=1),
            created_at=SET_CREATED_AT + timedelta(hours=1),
        )
        await _insert_scored_agent(
            conn,
            miner_hotkey="second-hotkey",
            final_score=0.49,
            cost_usd=0.01,
            approved_at=now - timedelta(hours=1),
            created_at=SET_CREATED_AT + timedelta(hours=2),
        )

    assert await get_weight_receiving_agent_hotkey() == "leader-hotkey"

    info = await get_weight_receiving_agent_info()
    assert info is not None
    assert info["miner_hotkey"] == "leader-hotkey"
    assert info["agent_id"] == leader_id


@pytest.mark.anyio
async def test_expired_top_scored_agent_burns_instead_of_falling_through():
    now = datetime.now(timezone.utc)
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn)
        await _insert_scored_agent(
            conn,
            miner_hotkey="expired-leader",
            final_score=0.50,
            cost_usd=0.10,
            approved_at=now - timedelta(hours=13),
            created_at=SET_CREATED_AT + timedelta(hours=1),
        )
        await _insert_scored_agent(
            conn,
            miner_hotkey="eligible-second",
            final_score=0.49,
            cost_usd=0.01,
            approved_at=now - timedelta(hours=1),
            created_at=SET_CREATED_AT + timedelta(hours=2),
        )

    assert await get_weight_receiving_agent_hotkey() is None
    assert await get_weight_receiving_agent_info() is None


@pytest.mark.anyio
async def test_unapproved_top_scored_agent_burns_instead_of_falling_through():
    now = datetime.now(timezone.utc)
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn)
        await _insert_scored_agent(
            conn,
            miner_hotkey="unapproved-leader",
            final_score=0.50,
            cost_usd=0.10,
            approved=False,
            approved_at=None,
            created_at=SET_CREATED_AT + timedelta(hours=1),
        )
        await _insert_scored_agent(
            conn,
            miner_hotkey="eligible-second",
            final_score=0.49,
            cost_usd=0.01,
            approved_at=now - timedelta(hours=1),
            created_at=SET_CREATED_AT + timedelta(hours=2),
        )

    assert await get_weight_receiving_agent_hotkey() is None


@pytest.mark.anyio
async def test_tied_scores_apply_cost_tiebreak_before_eligibility_window():
    now = datetime.now(timezone.utc)
    async with _db.pool.acquire() as conn:
        await _insert_eval_set(conn)
        await _insert_scored_agent(
            conn,
            miner_hotkey="expired-cheaper-leader",
            final_score=0.4489795918367347,
            cost_usd=0.06,
            approved_at=now - timedelta(hours=13),
            created_at=SET_CREATED_AT + timedelta(hours=1),
        )
        await _insert_scored_agent(
            conn,
            miner_hotkey="eligible-costlier-second",
            final_score=0.4489795918367347,
            cost_usd=0.08,
            approved_at=now - timedelta(hours=1),
            created_at=SET_CREATED_AT + timedelta(hours=2),
        )

    assert await get_weight_receiving_agent_hotkey() is None
