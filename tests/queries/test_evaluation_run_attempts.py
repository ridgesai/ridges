from datetime import datetime, timezone
from uuid import uuid4

import pytest

import utils.database as _db
from models.evaluation_run import EvaluationRunLogType, EvaluationRunStatus
from models.evaluation_set import EvaluationSetGroup, EvaluationSetProblem
from queries.evaluation import update_unfinished_evaluation_runs_in_evaluation_id_to_errored
from queries.evaluation_run import (
    check_if_evaluation_run_logs_exist,
    create_evaluation_run_log,
    create_evaluation_runs,
    get_all_evaluation_runs_in_evaluation_id,
    get_evaluation_run_by_id,
    get_evaluation_run_logs_by_id,
    get_evaluation_run_metrics_by_id,
    update_evaluation_run_by_id,
)
from queries.evaluation_run_attempt import (
    create_next_attempt_and_reset_evaluation_run,
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


async def _seed_single_run(conn):
    _, evaluation_id = await _seed_evaluation(conn)
    await create_evaluation_runs(evaluation_id, [_problem()])
    runs = await get_all_evaluation_runs_in_evaluation_id(evaluation_id)
    return evaluation_id, runs[0]


async def test_update_evaluation_run_dual_writes_current_attempt(postgres_db):
    async with _db.pool.acquire() as conn:
        _, run = await _seed_single_run(conn)

    run.status = EvaluationRunStatus.error
    run.error_code = 2000
    run.error_message = "boom"
    run.cost_usd = 1.25
    run.finished_or_errored_at = datetime.now(timezone.utc)
    await update_evaluation_run_by_id(run)

    (attempt,) = await get_attempts_for_evaluation_run(run.evaluation_run_id)
    assert attempt.status == EvaluationRunStatus.error
    assert attempt.error_code == 2000
    assert attempt.error_message == "boom"
    assert attempt.cost_usd == 1.25
    assert attempt.finished_or_errored_at is not None


async def test_create_next_attempt_resets_mirror(postgres_db):
    async with _db.pool.acquire() as conn:
        _, run = await _seed_single_run(conn)

    run.status = EvaluationRunStatus.error
    run.error_code = 2000
    run.error_message = "boom"
    run.patch = "some patch"
    run.finished_or_errored_at = datetime.now(timezone.utc)
    await update_evaluation_run_by_id(run)

    new_attempt = await create_next_attempt_and_reset_evaluation_run(run.evaluation_run_id)
    assert new_attempt.attempt_number == 2
    assert new_attempt.status == EvaluationRunStatus.pending

    mirrored = await get_evaluation_run_by_id(run.evaluation_run_id)
    assert mirrored.status == EvaluationRunStatus.pending
    assert mirrored.error_code is None
    assert mirrored.error_message is None
    assert mirrored.patch is None
    assert mirrored.cost_usd is None
    assert mirrored.finished_or_errored_at is None
    assert mirrored.started_initializing_agent_at is None

    attempts = await get_attempts_for_evaluation_run(run.evaluation_run_id)
    assert [a.attempt_number for a in attempts] == [1, 2]
    assert attempts[0].status == EvaluationRunStatus.error  # history preserved


async def test_dual_write_targets_latest_attempt_only(postgres_db):
    async with _db.pool.acquire() as conn:
        _, run = await _seed_single_run(conn)

    run.status = EvaluationRunStatus.error
    run.error_code = 2000
    run.error_message = "boom"
    run.finished_or_errored_at = datetime.now(timezone.utc)
    await update_evaluation_run_by_id(run)
    await create_next_attempt_and_reset_evaluation_run(run.evaluation_run_id)

    refreshed = await get_evaluation_run_by_id(run.evaluation_run_id)
    refreshed.status = EvaluationRunStatus.initializing_agent
    refreshed.started_initializing_agent_at = datetime.now(timezone.utc)
    await update_evaluation_run_by_id(refreshed)

    attempts = await get_attempts_for_evaluation_run(run.evaluation_run_id)
    assert attempts[0].status == EvaluationRunStatus.error  # untouched
    assert attempts[1].status == EvaluationRunStatus.initializing_agent


async def test_bulk_error_update_mirrors_current_attempt(postgres_db):
    async with _db.pool.acquire() as conn:
        evaluation_id, run = await _seed_single_run(conn)

    await update_unfinished_evaluation_runs_in_evaluation_id_to_errored(evaluation_id, "validator died")

    mirrored = await get_evaluation_run_by_id(run.evaluation_run_id)
    assert mirrored.status == EvaluationRunStatus.error

    (attempt,) = await get_attempts_for_evaluation_run(run.evaluation_run_id)
    assert attempt.status == EvaluationRunStatus.error
    assert attempt.error_code == 3000  # PLATFORM_RESTARTED_WHILE_PENDING
    assert attempt.error_message == "validator died"
    assert attempt.finished_or_errored_at is not None


async def test_logs_are_attempt_scoped(postgres_db):
    async with _db.pool.acquire() as conn:
        _, run = await _seed_single_run(conn)

    await create_evaluation_run_log(run.evaluation_run_id, EvaluationRunLogType.agent, "attempt 1 logs")
    assert await check_if_evaluation_run_logs_exist(run.evaluation_run_id, EvaluationRunLogType.agent) is True

    run.status = EvaluationRunStatus.error
    run.error_code = 2000
    run.error_message = "boom"
    run.finished_or_errored_at = datetime.now(timezone.utc)
    await update_evaluation_run_by_id(run)
    await create_next_attempt_and_reset_evaluation_run(run.evaluation_run_id)

    # A fresh attempt has no logs yet, so the insert-once guard must allow a new insert.
    assert await check_if_evaluation_run_logs_exist(run.evaluation_run_id, EvaluationRunLogType.agent) is False
    await create_evaluation_run_log(run.evaluation_run_id, EvaluationRunLogType.agent, "attempt 2 logs")

    # Reads return the latest attempt's logs; attempt 1 logs remain in the table.
    assert await get_evaluation_run_logs_by_id(run.evaluation_run_id, EvaluationRunLogType.agent) == "attempt 2 logs"
    async with _db.pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM evaluation_run_logs WHERE evaluation_run_id = $1", run.evaluation_run_id
        )
    assert count == 2


async def test_metrics_include_attempt_count(postgres_db):
    async with _db.pool.acquire() as conn:
        _, run = await _seed_single_run(conn)

    metrics = await get_evaluation_run_metrics_by_id(run.evaluation_run_id)
    assert metrics["attempt_count"] == 1

    run.status = EvaluationRunStatus.error
    run.error_code = 2000
    run.error_message = "boom"
    run.finished_or_errored_at = datetime.now(timezone.utc)
    await update_evaluation_run_by_id(run)
    await create_next_attempt_and_reset_evaluation_run(run.evaluation_run_id)

    metrics = await get_evaluation_run_metrics_by_id(run.evaluation_run_id)
    assert metrics["attempt_count"] == 2


async def test_metrics_attempt_count_defaults_to_one_for_legacy_run(postgres_db):
    async with _db.pool.acquire() as conn:
        _, evaluation_id = await _seed_evaluation(conn)
        legacy_run_id = uuid4()
        await conn.execute(
            "INSERT INTO evaluation_runs (evaluation_run_id, evaluation_id, problem_name, status, created_at)"
            " VALUES ($1, $2, 'prob-1', 'pending', NOW())",
            legacy_run_id,
            evaluation_id,
        )

    metrics = await get_evaluation_run_metrics_by_id(legacy_run_id)
    assert metrics["attempt_count"] == 1
