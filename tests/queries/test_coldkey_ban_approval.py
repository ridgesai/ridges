from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest

import utils.database as _db
from queries.approval import finish_agent_and_enqueue_approval
from queries.banned_coldkey import COLDKEY_BAN_LOCK_NAMESPACE


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db):
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE approval_jobs, agent_approval_states, banned_coldkeys, agents RESTART IDENTITY CASCADE"
        )
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE approval_jobs, agent_approval_states, banned_coldkeys, agents RESTART IDENTITY CASCADE"
        )


async def _insert_evaluating_agent(*, miner_coldkey: str | None) -> UUID:
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
            miner_coldkey,
        )
    return agent_id


async def _assert_finished_without_job(agent_id: UUID) -> None:
    async with _db.pool.acquire() as conn:
        status = await conn.fetchval("SELECT status::text FROM agents WHERE agent_id = $1", agent_id)
        job_count = await conn.fetchval("SELECT COUNT(*) FROM approval_jobs WHERE agent_id = $1", agent_id)
    assert status == "finished"
    assert job_count == 0


@pytest.mark.anyio
async def test_ban_winning_publication_race_finishes_agent_without_approval_job() -> None:
    miner_coldkey = "race-coldkey"
    agent_id = await _insert_evaluating_agent(miner_coldkey=miner_coldkey)

    async with _db.pool.acquire() as ban_conn:
        transaction = ban_conn.transaction()
        await transaction.start()
        await ban_conn.execute(
            "SELECT pg_advisory_xact_lock($1, hashtext($2))",
            COLDKEY_BAN_LOCK_NAMESPACE,
            miner_coldkey,
        )
        await ban_conn.execute(
            "INSERT INTO banned_coldkeys (miner_coldkey, banned_reason) VALUES ($1, 'test ban')",
            miner_coldkey,
        )

        approval_task = asyncio.create_task(
            finish_agent_and_enqueue_approval(
                agent_id=agent_id,
                set_id=7,
                policy_version="test-policy",
            )
        )

        for _ in range(100):
            if any("pg_advisory_xact_lock" in entry["query"] for entry in _db.DEBUG_QUERIES["running"]):
                break
            await asyncio.sleep(0.01)
        else:
            approval_task.cancel()
            raise AssertionError("Approval did not reach the coldkey publication lock")

        assert not approval_task.done()
        await transaction.commit()

    assert await approval_task is False
    await _assert_finished_without_job(agent_id)


@pytest.mark.anyio
async def test_null_coldkey_agent_can_still_enqueue_approval() -> None:
    agent_id = await _insert_evaluating_agent(miner_coldkey=None)

    enqueued = await finish_agent_and_enqueue_approval(
        agent_id=agent_id,
        set_id=7,
        policy_version="test-policy",
    )

    assert enqueued is True
    async with _db.pool.acquire() as conn:
        status = await conn.fetchval("SELECT status::text FROM agents WHERE agent_id = $1", agent_id)
        job_count = await conn.fetchval("SELECT COUNT(*) FROM approval_jobs WHERE agent_id = $1", agent_id)
    assert status == "finished"
    assert job_count == 1
