from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException

from api.endpoints import validator as validator_endpoint
from api.endpoints.validator_models import ValidatorUpdateEvaluationRunRequest
from models.evaluation_run import EvaluationRun, EvaluationRunLogType, EvaluationRunStatus
from models.problem import ProblemTestCategory, ProblemTestResult, ProblemTestResultStatus


def _test_result() -> ProblemTestResult:
    return ProblemTestResult(
        name="pass_case", category=ProblemTestCategory.default, status=ProblemTestResultStatus.PASS
    )


def _make_validator(evaluation_id):
    return validator_endpoint.Validator(
        session_id=uuid4(),
        name="validator-1",
        hotkey="hotkey",
        time_connected=datetime.now(timezone.utc),
        ip_address="127.0.0.1",
        current_evaluation_id=evaluation_id,
    )


def _make_evaluation_run(
    *,
    status: EvaluationRunStatus,
    patch: str | None = None,
    started_initializing_agent_at: datetime | None = None,
    started_running_agent_at: datetime | None = None,
    started_initializing_eval_at: datetime | None = None,
    started_running_eval_at: datetime | None = None,
):
    evaluation_id = uuid4()
    return EvaluationRun(
        evaluation_run_id=uuid4(),
        evaluation_id=evaluation_id,
        problem_name="update-status-file",
        status=status,
        patch=patch,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        started_initializing_agent_at=started_initializing_agent_at,
        started_running_agent_at=started_running_agent_at,
        started_initializing_eval_at=started_initializing_eval_at,
        started_running_eval_at=started_running_eval_at,
    )


def _install_endpoint_capture(
    monkeypatch, evaluation_run: EvaluationRun, *, existing_logs: set[EvaluationRunLogType] | None = None
):
    capture = {
        "updated_runs": [],
        "created_logs": [],
        "existing_logs": set(existing_logs or set()),
    }

    async def fake_get_evaluation_run_by_id(_evaluation_run_id):
        return evaluation_run

    async def fake_check_if_evaluation_run_logs_exist(_evaluation_run_id, log_type):
        return log_type in capture["existing_logs"]

    async def fake_create_evaluation_run_log(_evaluation_run_id, log_type, logs):
        capture["existing_logs"].add(log_type)
        capture["created_logs"].append({"type": log_type, "logs": logs})

    async def fake_update_evaluation_run_by_id(updated_evaluation_run):
        capture["updated_runs"].append(updated_evaluation_run.model_copy(deep=True))

    monkeypatch.setattr(validator_endpoint, "get_evaluation_run_by_id", fake_get_evaluation_run_by_id)
    monkeypatch.setattr(
        validator_endpoint, "check_if_evaluation_run_logs_exist", fake_check_if_evaluation_run_logs_exist
    )
    monkeypatch.setattr(validator_endpoint, "create_evaluation_run_log", fake_create_evaluation_run_log)
    monkeypatch.setattr(validator_endpoint, "update_evaluation_run_by_id", fake_update_evaluation_run_by_id)
    return capture


async def _call_update(request: ValidatorUpdateEvaluationRunRequest, validator):
    return await validator_endpoint.validator_update_evaluation_run.__wrapped__(request, validator=validator)


@pytest.mark.anyio
async def test_finished_from_running_eval_still_works(monkeypatch) -> None:
    earlier = datetime.now(timezone.utc) - timedelta(minutes=1)
    evaluation_run = _make_evaluation_run(
        status=EvaluationRunStatus.running_eval,
        patch="existing patch",
        started_initializing_agent_at=earlier - timedelta(minutes=3),
        started_running_agent_at=earlier - timedelta(minutes=2),
        started_initializing_eval_at=earlier - timedelta(minutes=1),
        started_running_eval_at=earlier,
    )
    capture = _install_endpoint_capture(monkeypatch, evaluation_run, existing_logs={EvaluationRunLogType.agent})
    validator = _make_validator(evaluation_run.evaluation_id)

    await _call_update(
        ValidatorUpdateEvaluationRunRequest(
            evaluation_run_id=evaluation_run.evaluation_run_id,
            updated_status=EvaluationRunStatus.finished,
            verifier_reward=1.0,
            test_results=[_test_result()],
            eval_logs="eval logs",
        ),
        validator,
    )

    assert evaluation_run.status == EvaluationRunStatus.finished
    assert evaluation_run.patch == "existing patch"
    assert evaluation_run.started_running_eval_at == earlier
    assert capture["created_logs"] == [{"type": EvaluationRunLogType.eval, "logs": "eval logs"}]
    assert capture["updated_runs"][-1].verifier_reward == 1.0


@pytest.mark.anyio
async def test_finished_from_initializing_eval_backfills_running_eval_timestamp(monkeypatch) -> None:
    started_initializing_eval_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    evaluation_run = _make_evaluation_run(
        status=EvaluationRunStatus.initializing_eval,
        patch="existing patch",
        started_initializing_agent_at=started_initializing_eval_at - timedelta(minutes=2),
        started_running_agent_at=started_initializing_eval_at - timedelta(minutes=1),
        started_initializing_eval_at=started_initializing_eval_at,
    )
    capture = _install_endpoint_capture(monkeypatch, evaluation_run, existing_logs={EvaluationRunLogType.agent})
    validator = _make_validator(evaluation_run.evaluation_id)

    await _call_update(
        ValidatorUpdateEvaluationRunRequest(
            evaluation_run_id=evaluation_run.evaluation_run_id,
            updated_status=EvaluationRunStatus.finished,
            verifier_reward=1.0,
            test_results=[_test_result()],
            eval_logs="eval logs",
        ),
        validator,
    )

    assert evaluation_run.status == EvaluationRunStatus.finished
    assert evaluation_run.started_running_eval_at == evaluation_run.finished_or_errored_at
    assert capture["created_logs"] == [{"type": EvaluationRunLogType.eval, "logs": "eval logs"}]


@pytest.mark.anyio
async def test_finished_from_running_agent_backfills_eval_stage_and_persists_patch_and_agent_logs(monkeypatch) -> None:
    started_running_agent_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    evaluation_run = _make_evaluation_run(
        status=EvaluationRunStatus.running_agent,
        started_initializing_agent_at=started_running_agent_at - timedelta(minutes=1),
        started_running_agent_at=started_running_agent_at,
    )
    capture = _install_endpoint_capture(monkeypatch, evaluation_run)
    validator = _make_validator(evaluation_run.evaluation_id)

    await _call_update(
        ValidatorUpdateEvaluationRunRequest(
            evaluation_run_id=evaluation_run.evaluation_run_id,
            updated_status=EvaluationRunStatus.finished,
            patch="new patch",
            agent_logs="agent logs",
            verifier_reward=1.0,
            test_results=[_test_result()],
            eval_logs="eval logs",
        ),
        validator,
    )

    assert evaluation_run.status == EvaluationRunStatus.finished
    assert evaluation_run.patch == "new patch"
    assert evaluation_run.started_initializing_eval_at == evaluation_run.finished_or_errored_at
    assert evaluation_run.started_running_eval_at == evaluation_run.finished_or_errored_at
    assert capture["created_logs"] == [
        {"type": EvaluationRunLogType.agent, "logs": "agent logs"},
        {"type": EvaluationRunLogType.eval, "logs": "eval logs"},
    ]


@pytest.mark.anyio
async def test_finished_from_initializing_agent_backfills_running_agent_and_eval_timestamps(monkeypatch) -> None:
    started_initializing_agent_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    evaluation_run = _make_evaluation_run(
        status=EvaluationRunStatus.initializing_agent,
        started_initializing_agent_at=started_initializing_agent_at,
    )
    _install_endpoint_capture(monkeypatch, evaluation_run)
    validator = _make_validator(evaluation_run.evaluation_id)

    await _call_update(
        ValidatorUpdateEvaluationRunRequest(
            evaluation_run_id=evaluation_run.evaluation_run_id,
            updated_status=EvaluationRunStatus.finished,
            patch="new patch",
            agent_logs="agent logs",
            verifier_reward=1.0,
            test_results=[_test_result()],
            eval_logs="eval logs",
        ),
        validator,
    )

    assert evaluation_run.started_initializing_agent_at == started_initializing_agent_at
    assert evaluation_run.started_running_agent_at == evaluation_run.finished_or_errored_at
    assert evaluation_run.started_initializing_eval_at == evaluation_run.finished_or_errored_at
    assert evaluation_run.started_running_eval_at == evaluation_run.finished_or_errored_at


@pytest.mark.anyio
async def test_finished_from_pending_backfills_all_missing_stage_timestamps(monkeypatch) -> None:
    evaluation_run = _make_evaluation_run(status=EvaluationRunStatus.pending)
    _install_endpoint_capture(monkeypatch, evaluation_run)
    validator = _make_validator(evaluation_run.evaluation_id)

    await _call_update(
        ValidatorUpdateEvaluationRunRequest(
            evaluation_run_id=evaluation_run.evaluation_run_id,
            updated_status=EvaluationRunStatus.finished,
            patch="new patch",
            agent_logs="agent logs",
            verifier_reward=1.0,
            test_results=[_test_result()],
            eval_logs="eval logs",
        ),
        validator,
    )

    assert evaluation_run.started_initializing_agent_at == evaluation_run.finished_or_errored_at
    assert evaluation_run.started_running_agent_at == evaluation_run.finished_or_errored_at
    assert evaluation_run.started_initializing_eval_at == evaluation_run.finished_or_errored_at
    assert evaluation_run.started_running_eval_at == evaluation_run.finished_or_errored_at


@pytest.mark.anyio
async def test_finished_requires_patch_if_none_is_persisted_or_provided(monkeypatch) -> None:
    evaluation_run = _make_evaluation_run(status=EvaluationRunStatus.running_agent)
    _install_endpoint_capture(monkeypatch, evaluation_run)
    validator = _make_validator(evaluation_run.evaluation_id)

    with pytest.raises(HTTPException, match="The patch is required when updating an evaluation run to finished"):
        await _call_update(
            ValidatorUpdateEvaluationRunRequest(
                evaluation_run_id=evaluation_run.evaluation_run_id,
                updated_status=EvaluationRunStatus.finished,
                agent_logs="agent logs",
                verifier_reward=1.0,
                test_results=[_test_result()],
                eval_logs="eval logs",
            ),
            validator,
        )


@pytest.mark.anyio
async def test_finished_requires_agent_logs_if_none_exist_or_are_provided(monkeypatch) -> None:
    evaluation_run = _make_evaluation_run(status=EvaluationRunStatus.running_agent, patch="existing patch")
    _install_endpoint_capture(monkeypatch, evaluation_run)
    validator = _make_validator(evaluation_run.evaluation_id)

    with pytest.raises(HTTPException, match="The agent logs are required when updating an evaluation run to finished"):
        await _call_update(
            ValidatorUpdateEvaluationRunRequest(
                evaluation_run_id=evaluation_run.evaluation_run_id,
                updated_status=EvaluationRunStatus.finished,
                verifier_reward=1.0,
                test_results=[_test_result()],
                eval_logs="eval logs",
            ),
            validator,
        )


@pytest.mark.anyio
async def test_finished_ignores_duplicate_agent_logs(monkeypatch) -> None:
    evaluation_run = _make_evaluation_run(status=EvaluationRunStatus.running_agent, patch="existing patch")
    capture = _install_endpoint_capture(monkeypatch, evaluation_run, existing_logs={EvaluationRunLogType.agent})
    validator = _make_validator(evaluation_run.evaluation_id)

    await _call_update(
        ValidatorUpdateEvaluationRunRequest(
            evaluation_run_id=evaluation_run.evaluation_run_id,
            updated_status=EvaluationRunStatus.finished,
            patch="different patch",
            agent_logs="duplicate agent logs",
            verifier_reward=1.0,
            test_results=[_test_result()],
            eval_logs="eval logs",
        ),
        validator,
    )

    assert evaluation_run.patch == "existing patch"
    assert capture["created_logs"] == [{"type": EvaluationRunLogType.eval, "logs": "eval logs"}]


@pytest.mark.anyio
async def test_error_ignores_duplicate_agent_and_eval_logs(monkeypatch) -> None:
    evaluation_run = _make_evaluation_run(status=EvaluationRunStatus.running_eval, patch="existing patch")
    capture = _install_endpoint_capture(
        monkeypatch,
        evaluation_run,
        existing_logs={EvaluationRunLogType.agent, EvaluationRunLogType.eval},
    )
    validator = _make_validator(evaluation_run.evaluation_id)

    await _call_update(
        ValidatorUpdateEvaluationRunRequest(
            evaluation_run_id=evaluation_run.evaluation_run_id,
            updated_status=EvaluationRunStatus.error,
            error_code=1234,
            error_message="boom",
            agent_logs="duplicate agent logs",
            eval_logs="duplicate eval logs",
        ),
        validator,
    )

    assert evaluation_run.status == EvaluationRunStatus.error
    assert evaluation_run.error_code == 1234
    assert evaluation_run.error_message == "boom"
    assert capture["created_logs"] == []
