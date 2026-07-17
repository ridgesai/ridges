from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

import api.config as config
from api.endpoints import validator as validator_endpoint
from api.endpoints.validator_models import ValidatorUpdateEvaluationRunRequest
from models.agent import Agent, AgentStatus
from models.evaluation import Evaluation
from models.evaluation_run import (
    EvaluationRun,
    EvaluationRunAttempt,
    EvaluationRunErrorCode,
    EvaluationRunStatus,
)
from models.evaluation_set import EvaluationSetGroup

pytestmark = pytest.mark.anyio


def _make_setup(monkeypatch, *, agent_status=AgentStatus.evaluating, attempt_count=1):
    evaluation_id = uuid4()
    agent_id = uuid4()

    evaluation_run = EvaluationRun(
        evaluation_run_id=uuid4(),
        evaluation_id=evaluation_id,
        problem_name="prob-1",
        status=EvaluationRunStatus.running_agent,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        started_initializing_agent_at=datetime.now(timezone.utc) - timedelta(minutes=4),
        started_running_agent_at=datetime.now(timezone.utc) - timedelta(minutes=3),
    )

    evaluation = Evaluation(
        evaluation_id=evaluation_id,
        agent_id=agent_id,
        validator_hotkey="validator-hotkey",
        set_id=1,
        evaluation_set_group=EvaluationSetGroup.validator,
        created_at=datetime.now(timezone.utc),
    )

    agent = Agent(
        agent_id=agent_id,
        miner_hotkey="5FakeHotkey",
        name="agent-a",
        version_num=1,
        status=agent_status,
        created_at=datetime.now(timezone.utc),
    )

    validator = validator_endpoint.Validator(
        session_id=uuid4(),
        name="validator-1",
        hotkey="validator-hotkey",
        time_connected=datetime.now(timezone.utc),
        ip_address="127.0.0.1",
        current_evaluation_id=evaluation_id,
        current_evaluation=evaluation,
        current_agent=agent,
    )

    capture = {"created_attempts": 0}

    async def fake_get_evaluation_run_by_id(_run_id):
        return evaluation_run

    async def fake_update_evaluation_run_by_id(_run):
        return None

    async def fake_create_evaluation_run_log(_run_id, _type, _logs):
        return None

    async def fake_check_if_evaluation_run_logs_exist(_run_id, _type):
        return True

    async def fake_get_agent_by_id(_agent_id):
        return agent

    async def fake_maybe_stop_agent_by_score_bound(_evaluation):
        return False

    async def fake_get_attempt_count(_run_id):
        return attempt_count

    async def fake_create_next_attempt(run_id):
        capture["created_attempts"] += 1
        return EvaluationRunAttempt(
            attempt_id=uuid4(),
            evaluation_run_id=run_id,
            attempt_number=attempt_count + 1,
            status=EvaluationRunStatus.pending,
            created_at=datetime.now(timezone.utc),
        )

    async def fake_generate_presigned_upload_url(_s3_key):
        return "https://s3.example.com/fresh-upload-url"

    monkeypatch.setattr(validator_endpoint, "get_evaluation_run_by_id", fake_get_evaluation_run_by_id)
    monkeypatch.setattr(validator_endpoint, "update_evaluation_run_by_id", fake_update_evaluation_run_by_id)
    monkeypatch.setattr(validator_endpoint, "create_evaluation_run_log", fake_create_evaluation_run_log)
    monkeypatch.setattr(
        validator_endpoint, "check_if_evaluation_run_logs_exist", fake_check_if_evaluation_run_logs_exist
    )
    monkeypatch.setattr(validator_endpoint, "get_agent_by_id", fake_get_agent_by_id)
    monkeypatch.setattr(validator_endpoint, "_maybe_stop_agent_by_score_bound", fake_maybe_stop_agent_by_score_bound)
    monkeypatch.setattr(validator_endpoint, "get_attempt_count_for_evaluation_run", fake_get_attempt_count)
    monkeypatch.setattr(validator_endpoint, "create_next_attempt_and_reset_evaluation_run", fake_create_next_attempt)
    monkeypatch.setattr(validator_endpoint, "generate_presigned_upload_url", fake_generate_presigned_upload_url)

    return evaluation_run, validator, capture


def _error_request(evaluation_run, error_code):
    return ValidatorUpdateEvaluationRunRequest(
        evaluation_run_id=evaluation_run.evaluation_run_id,
        updated_status=EvaluationRunStatus.error,
        error_code=int(error_code),
        error_message="boom",
        agent_logs="agent logs",
        eval_logs="eval logs",
    )


async def _call_update(request, validator):
    return await validator_endpoint.validator_update_evaluation_run.__wrapped__(request, validator=validator)


async def test_retryable_error_grants_retry(monkeypatch) -> None:
    evaluation_run, validator, capture = _make_setup(monkeypatch)

    response = await _call_update(
        _error_request(evaluation_run, EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR), validator
    )

    assert response.retry is True
    assert response.attempt_number == 2
    assert response.artifact_upload_url == "https://s3.example.com/fresh-upload-url"
    assert capture["created_attempts"] == 1


async def test_agent_error_is_not_retried(monkeypatch) -> None:
    evaluation_run, validator, capture = _make_setup(monkeypatch)

    response = await _call_update(_error_request(evaluation_run, EvaluationRunErrorCode.AGENT_INVALID_PATCH), validator)

    assert response.retry is False
    assert capture["created_attempts"] == 0


async def test_attempt_cap_blocks_retry(monkeypatch) -> None:
    evaluation_run, validator, capture = _make_setup(monkeypatch, attempt_count=config.MAX_ATTEMPTS_PER_EVALUATION_RUN)

    response = await _call_update(
        _error_request(evaluation_run, EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR), validator
    )

    assert response.retry is False
    assert capture["created_attempts"] == 0


async def test_legacy_run_without_attempts_is_not_retried(monkeypatch) -> None:
    evaluation_run, validator, capture = _make_setup(monkeypatch, attempt_count=0)

    response = await _call_update(
        _error_request(evaluation_run, EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR), validator
    )

    assert response.retry is False
    assert capture["created_attempts"] == 0


async def test_stopped_agent_blocks_retry(monkeypatch) -> None:
    evaluation_run, validator, capture = _make_setup(monkeypatch, agent_status=AgentStatus.cancelled)

    response = await _call_update(
        _error_request(evaluation_run, EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR), validator
    )

    assert response.retry is False
    assert capture["created_attempts"] == 0


async def test_finished_update_never_retries(monkeypatch) -> None:
    from models.problem import ProblemTestCategory, ProblemTestResult, ProblemTestResultStatus

    evaluation_run, validator, capture = _make_setup(monkeypatch)
    evaluation_run.patch = "existing patch"

    response = await _call_update(
        ValidatorUpdateEvaluationRunRequest(
            evaluation_run_id=evaluation_run.evaluation_run_id,
            updated_status=EvaluationRunStatus.finished,
            verifier_reward=1.0,
            test_results=[
                ProblemTestResult(name="t", category=ProblemTestCategory.default, status=ProblemTestResultStatus.PASS)
            ],
            eval_logs="eval logs",
        ),
        validator,
    )

    assert response.retry is False
    assert capture["created_attempts"] == 0
