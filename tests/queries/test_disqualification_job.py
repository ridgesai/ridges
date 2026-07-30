from datetime import datetime, timezone
from uuid import uuid4

import pytest

import utils.database as _db
from models.disqualification_job import DisqualificationJob
from queries.disqualification_job import (
    claim_next_pending_disqualification_job,
    count_pending_disqualification_jobs,
    enqueue_disqualification_job,
    mark_disqualification_job_processed,
)

SET_ID = 71


def test_disqualification_job_model_roundtrips():
    now = datetime.now(timezone.utc)
    agent_id = uuid4()
    job_id = uuid4()
    job = DisqualificationJob(
        id=job_id,
        agent_id=agent_id,
        set_id=71,
        created_at=now,
        processed_at=None,
        attempts=0,
        error=None,
    )
    assert job.id == job_id
    assert job.agent_id == agent_id
    assert job.set_id == 71
    assert job.processed_at is None
    assert job.attempts == 0
    assert job.error is None


@pytest.fixture
async def clean_jobs(postgres_db):
    async with _db.pool.acquire() as conn:
        await conn.execute("TRUNCATE disqualification_jobs, agents RESTART IDENTITY CASCADE")
    yield


async def _insert_agent(conn, agent_id):
    await conn.execute(
        """
        INSERT INTO agents (agent_id, miner_hotkey, name, version_num, status, created_at, ip_address)
        VALUES ($1, 'hk', 'hk', 1, 'finished', NOW(), '127.0.0.1')
        """,
        agent_id,
    )


@pytest.mark.anyio
async def test_enqueue_is_deduped_while_pending(clean_jobs):
    agent_id = uuid4()
    async with _db.pool.acquire() as conn:
        await _insert_agent(conn, agent_id)
        async with conn.transaction():
            first = await enqueue_disqualification_job(conn, agent_id=agent_id, set_id=SET_ID)
            second = await enqueue_disqualification_job(conn, agent_id=agent_id, set_id=SET_ID)
    assert first is not None
    assert second is None


@pytest.mark.anyio
async def test_claim_marks_attempts_and_mark_processed(clean_jobs):
    agent_id = uuid4()
    async with _db.pool.acquire() as conn:
        await _insert_agent(conn, agent_id)
        async with conn.transaction():
            await enqueue_disqualification_job(conn, agent_id=agent_id, set_id=SET_ID)

    assert await count_pending_disqualification_jobs() == 1

    async with _db.pool.acquire() as conn:
        async with conn.transaction():
            job = await claim_next_pending_disqualification_job.__wrapped__(conn)
            assert job is not None
            assert job["agent_id"] == agent_id
            assert job["set_id"] == SET_ID
            await mark_disqualification_job_processed.__wrapped__(conn, job["id"])

    assert await count_pending_disqualification_jobs() == 0
