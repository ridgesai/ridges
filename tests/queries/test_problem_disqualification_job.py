import pytest

import utils.database as _db
from queries.problem_disqualification_job import (
    claim_next_pending_problem_disqualification_job,
    count_pending_problem_disqualification_jobs,
    enqueue_problem_disqualification_job,
    mark_problem_disqualification_job_processed,
)

SET_ID = 71


@pytest.fixture
async def clean_jobs(postgres_db):
    async with _db.pool.acquire() as conn:
        await conn.execute("TRUNCATE problem_disqualification_jobs, evaluation_sets RESTART IDENTITY CASCADE")
    yield


async def _insert_set_problem(conn, name, group="validator"):
    await conn.execute(
        """
        INSERT INTO evaluation_sets (set_id, set_group, problem_name, benchmark_family, created_at)
        VALUES ($1, $2, $3, 'swebench', NOW())
        """,
        SET_ID,
        group,
        name,
    )


@pytest.mark.anyio
async def test_enqueue_is_deduped_while_pending(clean_jobs):
    async with _db.pool.acquire() as conn:
        await _insert_set_problem(conn, "flaky")
        async with conn.transaction():
            first = await enqueue_problem_disqualification_job(
                conn, set_id=SET_ID, set_group="validator", problem_name="flaky"
            )
            second = await enqueue_problem_disqualification_job(
                conn, set_id=SET_ID, set_group="validator", problem_name="flaky"
            )
    assert first is not None
    assert second is None


@pytest.mark.anyio
async def test_claim_marks_attempts_and_mark_processed(clean_jobs):
    async with _db.pool.acquire() as conn:
        await _insert_set_problem(conn, "flaky")
        async with conn.transaction():
            await enqueue_problem_disqualification_job(conn, set_id=SET_ID, set_group="validator", problem_name="flaky")

    assert await count_pending_problem_disqualification_jobs() == 1

    async with _db.pool.acquire() as conn:
        async with conn.transaction():
            job = await claim_next_pending_problem_disqualification_job.__wrapped__(conn)
            assert job is not None
            assert job["set_id"] == SET_ID
            assert job["problem_name"] == "flaky"
            await mark_problem_disqualification_job_processed.__wrapped__(conn, job["id"])

    assert await count_pending_problem_disqualification_jobs() == 0


@pytest.mark.anyio
async def test_claim_excludes_attempted_ids(clean_jobs):
    async with _db.pool.acquire() as conn:
        await _insert_set_problem(conn, "a")
        await _insert_set_problem(conn, "b")
        async with conn.transaction():
            await enqueue_problem_disqualification_job(conn, set_id=SET_ID, set_group="validator", problem_name="a")
            await enqueue_problem_disqualification_job(conn, set_id=SET_ID, set_group="validator", problem_name="b")

    async with _db.pool.acquire() as conn:
        async with conn.transaction():
            first = await claim_next_pending_problem_disqualification_job.__wrapped__(conn)
            second = await claim_next_pending_problem_disqualification_job.__wrapped__(conn, [first["id"]])
            assert second is not None
            assert second["id"] != first["id"]
