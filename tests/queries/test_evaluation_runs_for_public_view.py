import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

import utils.database as _db
from models.problem import ProblemTestCategory, ProblemTestResult, ProblemTestResultStatus
from queries.evaluation_run import get_all_evaluation_runs_in_evaluation_id, get_evaluation_runs_for_public_view
from utils.public_view import to_public_run

pytestmark = pytest.mark.anyio

PATCH = "diff --git a/tests/test_generated.py b/tests/test_generated.py\n+def test_generated(): ...\n"


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db):
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE evaluation_runs, evaluations, agents, evaluation_sets, competitions RESTART IDENTITY CASCADE"
        )


async def _seed_finished_run(conn):
    agent_id, evaluation_id, evaluation_run_id = uuid4(), uuid4(), uuid4()
    await conn.execute(
        "INSERT INTO evaluation_sets (set_id, set_group, problem_name, created_at)"
        " VALUES (1, 'validator', 'prob-1', $1) ON CONFLICT DO NOTHING",
        datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    await conn.execute(
        "INSERT INTO agents (agent_id, miner_hotkey, name, version_num, status, created_at, ip_address, set_id)"
        " VALUES ($1, '5FakeHotkey', 'agent-a', 1, 'finished', NOW(), '127.0.0.1', 1)",
        agent_id,
    )
    await conn.execute(
        "INSERT INTO evaluations (evaluation_id, agent_id, validator_hotkey, set_id, created_at,"
        " evaluation_set_group) VALUES ($1, $2, 'validator-hotkey', 1, NOW(), 'validator')",
        evaluation_id,
        agent_id,
    )
    test_results = [
        ProblemTestResult(
            name="tests/test_generated.py::test_generated",
            category=ProblemTestCategory.fail_to_pass,
            status=ProblemTestResultStatus.PASS,
        ).model_dump(mode="json")
    ]
    await conn.execute(
        """
        INSERT INTO evaluation_runs (
            evaluation_run_id, evaluation_id, problem_name, benchmark_family, status, patch, test_results,
            execution_spec, error_message, verifier_reward, cost_usd, created_at, finished_or_errored_at
        )
        VALUES ($1, $2, 'prob-1', 'ridges', 'finished', $3, $4, $5, 'Traceback (most recent call last)',
                1.0, 0.01, NOW(), NOW())
        """,
        evaluation_run_id,
        evaluation_id,
        PATCH,
        json.dumps(test_results),
        json.dumps({"s3_key": "tasks/prob-1.tar.gz"}),
    )
    return evaluation_id


async def test_public_view_runs_skip_dropped_columns_and_render_identically():
    async with _db.pool.acquire() as conn:
        evaluation_id = await _seed_finished_run(conn)

    [full_run] = await get_all_evaluation_runs_in_evaluation_id(evaluation_id)
    [public_view_run] = await get_evaluation_runs_for_public_view(evaluation_id=evaluation_id)

    assert full_run.patch == PATCH
    assert public_view_run.patch is None
    assert public_view_run.execution_spec is None
    assert public_view_run.error_message is None
    assert to_public_run(public_view_run) == to_public_run(full_run)
