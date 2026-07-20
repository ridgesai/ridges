from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

import utils.database as _db
from api.endpoints import validator as validator_endpoint
from api.endpoints.validator_models import ValidatorUpdateEvaluationRunRequest
from models.evaluation_run import EvaluationRunErrorCode, EvaluationRunStatus
from models.evaluation_set import EvaluationSetGroup, EvaluationSetProblem
from models.problem import ProblemTestCategory, ProblemTestResult, ProblemTestResultStatus
from queries.agent import get_agent_by_id
from queries.evaluation import get_evaluation_by_id, get_hydrated_evaluation_by_id
from queries.evaluation_run import create_evaluation_runs, get_all_evaluation_runs_in_evaluation_id
from queries.evaluation_run_attempt import get_attempts_for_evaluation_run

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db):
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE evaluation_run_logs, evaluation_run_attempts, evaluation_runs, evaluations,"
            " agents, evaluation_sets RESTART IDENTITY CASCADE"
        )


async def _seed(conn):
    agent_id = uuid4()
    await conn.execute(
        "INSERT INTO evaluation_sets (set_id, set_group, problem_name, created_at)"
        " VALUES (1, 'validator', 'prob-1', $1)",
        datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    await conn.execute(
        "INSERT INTO agents (agent_id, miner_hotkey, name, version_num, status, created_at, ip_address)"
        " VALUES ($1, '5FakeHotkey', 'agent-a', 1, 'evaluating', NOW(), '127.0.0.1')",
        agent_id,
    )
    evaluation_id = uuid4()
    await conn.execute(
        "INSERT INTO evaluations (evaluation_id, agent_id, validator_hotkey, set_id, created_at,"
        " evaluation_set_group) VALUES ($1, $2, 'validator-hotkey', 1, NOW(), 'validator')",
        evaluation_id,
        agent_id,
    )
    return agent_id, evaluation_id


async def _make_session(evaluation_id, agent_id):
    evaluation = await get_evaluation_by_id(evaluation_id)
    agent = await get_agent_by_id(agent_id)
    return validator_endpoint.Validator(
        session_id=uuid4(),
        name="validator-1",
        hotkey="validator-hotkey",
        time_connected=datetime.now(timezone.utc),
        ip_address="127.0.0.1",
        current_evaluation_id=evaluation_id,
        current_evaluation=evaluation,
        current_agent=agent,
    )


async def _update(validator, run_id, status, **extra):
    request = ValidatorUpdateEvaluationRunRequest(evaluation_run_id=run_id, updated_status=status, **extra)
    return await validator_endpoint.validator_update_evaluation_run.__wrapped__(request, validator=validator)


async def test_errored_run_is_retried_and_evaluation_succeeds(monkeypatch, postgres_db):
    async def fake_presign(_s3_key):
        return "https://s3.example.com/fresh-upload-url"

    monkeypatch.setattr(validator_endpoint, "generate_presigned_upload_url", fake_presign)

    async with _db.pool.acquire() as conn:
        agent_id, evaluation_id = await _seed(conn)

    await create_evaluation_runs(
        evaluation_id,
        [
            EvaluationSetProblem(
                set_id=1,
                set_group=EvaluationSetGroup.validator,
                problem_name="prob-1",
                created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
            )
        ],
    )
    (run,) = await get_all_evaluation_runs_in_evaluation_id(evaluation_id)
    validator = await _make_session(evaluation_id, agent_id)

    # Attempt 1 fails with a retryable validator error.
    await _update(validator, run.evaluation_run_id, EvaluationRunStatus.initializing_agent)
    response = await _update(
        validator,
        run.evaluation_run_id,
        EvaluationRunStatus.error,
        error_code=int(EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR),
        error_message="transient failure",
        agent_logs="attempt 1 agent logs",
        eval_logs="attempt 1 eval logs",
    )
    assert response.retry is True
    assert response.attempt_number == 2
    assert response.artifact_upload_url == "https://s3.example.com/fresh-upload-url"

    hydrated = await get_hydrated_evaluation_by_id(evaluation_id)
    assert hydrated.status.value == "running"  # mirror reset keeps the evaluation open

    # While the retried run is pending, finish-evaluation must refuse to close the evaluation.
    from fastapi import HTTPException

    from api.endpoints.validator_models import ValidatorFinishEvaluationRequest

    with pytest.raises(HTTPException) as exc_info:
        await validator_endpoint.validator_finish_evaluation.__wrapped__(
            ValidatorFinishEvaluationRequest(), validator=validator
        )
    assert exc_info.value.status_code == 409

    # Attempt 2 walks the full state machine and finishes solved.
    await _update(validator, run.evaluation_run_id, EvaluationRunStatus.initializing_agent)
    await _update(validator, run.evaluation_run_id, EvaluationRunStatus.running_agent)
    await _update(
        validator,
        run.evaluation_run_id,
        EvaluationRunStatus.initializing_eval,
        patch="the patch",
        agent_logs="attempt 2 agent logs",
    )
    await _update(validator, run.evaluation_run_id, EvaluationRunStatus.running_eval)
    response = await _update(
        validator,
        run.evaluation_run_id,
        EvaluationRunStatus.finished,
        verifier_reward=1.0,
        test_results=[
            ProblemTestResult(name="t", category=ProblemTestCategory.default, status=ProblemTestResultStatus.PASS)
        ],
        eval_logs="attempt 2 eval logs",
    )
    assert response.retry is False

    # The eval contract is preserved: the evaluation is a success scored from the final attempt.
    hydrated = await get_hydrated_evaluation_by_id(evaluation_id)
    assert hydrated.status.value == "success"
    assert hydrated.score == 1.0

    attempts = await get_attempts_for_evaluation_run(run.evaluation_run_id)
    assert [a.attempt_number for a in attempts] == [1, 2]
    assert attempts[0].status == EvaluationRunStatus.error
    assert attempts[1].status == EvaluationRunStatus.finished


async def test_retry_denied_once_attempt_cap_reached(monkeypatch, postgres_db):
    import api.config as config

    async def fake_presign(_s3_key):
        return "https://s3.example.com/fresh-upload-url"

    monkeypatch.setattr(validator_endpoint, "generate_presigned_upload_url", fake_presign)
    monkeypatch.setattr(config, "MAX_ATTEMPTS_PER_EVALUATION_RUN", 2)

    async with _db.pool.acquire() as conn:
        agent_id, evaluation_id = await _seed(conn)

    await create_evaluation_runs(
        evaluation_id,
        [
            EvaluationSetProblem(
                set_id=1,
                set_group=EvaluationSetGroup.validator,
                problem_name="prob-1",
                created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
            )
        ],
    )
    (run,) = await get_all_evaluation_runs_in_evaluation_id(evaluation_id)
    validator = await _make_session(evaluation_id, agent_id)

    def _error_kwargs(n):
        return dict(
            error_code=int(EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR),
            error_message=f"failure {n}",
            agent_logs=f"attempt {n} agent logs",
            eval_logs=f"attempt {n} eval logs",
        )

    response = await _update(validator, run.evaluation_run_id, EvaluationRunStatus.error, **_error_kwargs(1))
    assert response.retry is True  # attempt 2 granted

    response = await _update(validator, run.evaluation_run_id, EvaluationRunStatus.error, **_error_kwargs(2))
    assert response.retry is False  # cap of 2 reached

    hydrated = await get_hydrated_evaluation_by_id(evaluation_id)
    assert hydrated.status.value == "failure"  # falls back to today's full-restart path
