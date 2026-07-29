from datetime import datetime, timezone
from uuid import uuid4

import pytest

import utils.database as _db
from models.disqualified_problem import DisqualifiedProblem
from models.evaluation_set import EvaluationSetGroup
from models.problem_disqualification_job import ProblemDisqualificationJob
from queries.disqualified_problem import (
    disqualify_problem,
    get_disqualified_problem,
    problem_exists_in_set,
)


def test_disqualified_problem_model_roundtrips():
    now = datetime.now(timezone.utc)
    p = DisqualifiedProblem(
        set_id=71,
        set_group=EvaluationSetGroup.validator,
        problem_name="flaky_task",
        reason="flaky harness",
        created_at=now,
    )
    assert p.set_id == 71
    assert p.set_group == EvaluationSetGroup.validator
    assert p.problem_name == "flaky_task"


def test_problem_disqualification_job_model_roundtrips():
    now = datetime.now(timezone.utc)
    job_id = uuid4()
    job = ProblemDisqualificationJob(
        id=job_id,
        set_id=71,
        set_group=EvaluationSetGroup.validator,
        problem_name="flaky_task",
        created_at=now,
        processed_at=None,
        attempts=0,
        error=None,
    )
    assert job.id == job_id
    assert job.set_group == EvaluationSetGroup.validator
    assert job.attempts == 0


SET_ID = 71


@pytest.fixture
async def clean_problems(postgres_db):
    async with _db.pool.acquire() as conn:
        await conn.execute("TRUNCATE disqualified_problems, evaluation_sets RESTART IDENTITY CASCADE")
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
async def test_disqualify_problem_inserts_and_is_readable(clean_problems):
    async with _db.pool.acquire() as conn:
        await _insert_set_problem(conn, "flaky_task")
        async with conn.transaction():
            dq = await disqualify_problem(
                conn, set_id=SET_ID, set_group="validator", problem_name="flaky_task", reason="flaky"
            )
    assert dq.problem_name == "flaky_task"
    got = await get_disqualified_problem(SET_ID, "validator", "flaky_task")
    assert got is not None and got.reason == "flaky"


@pytest.mark.anyio
async def test_problem_exists_distinguishes_set_group(clean_problems):
    async with _db.pool.acquire() as conn:
        await _insert_set_problem(conn, "shared_name", group="validator")
        await _insert_set_problem(conn, "shared_name", group="screener_1")
    assert await problem_exists_in_set(SET_ID, "validator", "shared_name") is True
    assert await problem_exists_in_set(SET_ID, "screener_1", "shared_name") is True
    assert await problem_exists_in_set(SET_ID, "validator", "does_not_exist") is False
