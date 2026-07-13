from datetime import datetime, timezone
from uuid import uuid4

import pytest

import utils.database as _db
from models.evaluation_run import EvaluationRunStatus
from models.evaluation_set import EvaluationSetGroup, EvaluationSetProblem
from queries.evaluation_run import create_evaluation_runs, get_all_evaluation_runs_in_evaluation_id
from queries.evaluation_run_attempt import (
    get_attempt_count_for_evaluation_run,
    get_attempts_for_evaluation_run,
)

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db):
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE evaluation_run_logs, evaluation_run_attempts, evaluation_runs, evaluations,"
            " agents, evaluation_sets RESTART IDENTITY CASCADE"
        )


async def _seed_evaluation(conn, *, agent_status: str = "evaluating"):
    """Insert the minimal agent + evaluation pair that evaluation runs hang off."""
    agent_id = uuid4()
    await conn.execute(
        "INSERT INTO evaluation_sets (set_id, set_group, problem_name, created_at)"
        " VALUES (1, 'validator', 'prob-1', $1) ON CONFLICT DO NOTHING",
        datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    await conn.execute(
        "INSERT INTO agents (agent_id, miner_hotkey, name, version_num, status, created_at, ip_address)"
        " VALUES ($1, '5FakeHotkey', 'agent-a', 1, $2, NOW(), '127.0.0.1')",
        agent_id,
        agent_status,
    )
    evaluation_id = uuid4()
    await conn.execute(
        "INSERT INTO evaluations (evaluation_id, agent_id, validator_hotkey, set_id, created_at,"
        " evaluation_set_group) VALUES ($1, $2, 'validator-hotkey', 1, NOW(), 'validator')",
        evaluation_id,
        agent_id,
    )
    return agent_id, evaluation_id


def _problem(name: str = "prob-1") -> EvaluationSetProblem:
    return EvaluationSetProblem(
        set_id=1,
        set_group=EvaluationSetGroup.validator,
        problem_name=name,
        created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )


async def test_create_evaluation_runs_creates_attempt_one(postgres_db):
    async with _db.pool.acquire() as conn:
        _, evaluation_id = await _seed_evaluation(conn)

    await create_evaluation_runs(evaluation_id, [_problem()])
    runs = await get_all_evaluation_runs_in_evaluation_id(evaluation_id)
    assert len(runs) == 1

    attempts = await get_attempts_for_evaluation_run(runs[0].evaluation_run_id)
    assert len(attempts) == 1
    assert attempts[0].attempt_number == 1
    assert attempts[0].status == EvaluationRunStatus.pending
    assert await get_attempt_count_for_evaluation_run(runs[0].evaluation_run_id) == 1


async def test_attempt_count_is_zero_for_legacy_run(postgres_db):
    async with _db.pool.acquire() as conn:
        _, evaluation_id = await _seed_evaluation(conn)
        legacy_run_id = uuid4()
        await conn.execute(
            "INSERT INTO evaluation_runs (evaluation_run_id, evaluation_id, problem_name, status, created_at)"
            " VALUES ($1, $2, 'prob-1', 'pending', NOW())",
            legacy_run_id,
            evaluation_id,
        )

    assert await get_attempt_count_for_evaluation_run(legacy_run_id) == 0
    assert await get_attempts_for_evaluation_run(legacy_run_id) == []
