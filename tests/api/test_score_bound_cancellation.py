from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException

from api.endpoints import validator as validator_endpoint
from api.endpoints.validator_models import (
    ValidatorCancelCurrentEvaluationRequest,
    ValidatorCheckCancellationRequest,
    ValidatorUpdateEvaluationRunRequest,
)
from models.agent import Agent, AgentStatus
from models.evaluation import Evaluation
from models.evaluation_run import EvaluationRun, EvaluationRunStatus
from models.evaluation_set import EvaluationSetGroup
from queries.evaluation import LocalEvaluationScoreBound, get_local_evaluation_score_upper_bound


def _agent(agent_id, *, status: AgentStatus = AgentStatus.evaluating) -> Agent:
    return Agent(
        agent_id=agent_id,
        miner_hotkey="miner-hotkey",
        name="agent",
        version_num=1,
        status=status,
        created_at=datetime.now(timezone.utc),
    )


def _evaluation(
    agent_id,
    *,
    group: EvaluationSetGroup = EvaluationSetGroup.validator,
    finished_at: datetime | None = None,
) -> Evaluation:
    return Evaluation(
        evaluation_id=uuid4(),
        agent_id=agent_id,
        validator_hotkey="validator-hotkey",
        set_id=21,
        evaluation_set_group=group,
        created_at=datetime.now(timezone.utc),
        finished_at=finished_at,
    )


def _validator(evaluation: Evaluation, agent: Agent) -> validator_endpoint.Validator:
    return validator_endpoint.Validator(
        session_id=uuid4(),
        name="validator",
        hotkey="validator-hotkey",
        time_connected=datetime.now(timezone.utc),
        ip_address="127.0.0.1",
        current_evaluation_id=evaluation.evaluation_id,
        current_evaluation=evaluation,
        current_agent=agent,
    )


@pytest.mark.anyio
async def test_score_bound_pruning_cancels_when_upper_bound_is_below_leader(monkeypatch) -> None:
    agent_id = uuid4()
    evaluation = _evaluation(agent_id)
    status_updates: list = []

    async def fake_get_agent_by_id(_agent_id):
        return _agent(_agent_id)

    async def fake_get_leader_score(_set_id, _excluded_agent_id, _required_validator_count):
        return 0.7333333333333333

    async def fake_get_upper_bound(_evaluation_id):
        return LocalEvaluationScoreBound(total_runs=20, impossible_runs=10, upper_bound=0.5)

    async def fake_transition_agent_status_if_matches(_agent_id, expected_status, new_status):
        status_updates.append((_agent_id, expected_status, new_status))
        return True

    monkeypatch.setattr(validator_endpoint, "get_agent_by_id", fake_get_agent_by_id)
    monkeypatch.setattr(validator_endpoint, "get_approved_validator_leader_score_for_set", fake_get_leader_score)
    monkeypatch.setattr(validator_endpoint, "get_local_evaluation_score_upper_bound", fake_get_upper_bound)
    monkeypatch.setattr(
        validator_endpoint, "transition_agent_status_if_matches", fake_transition_agent_status_if_matches
    )

    assert await validator_endpoint._maybe_stop_agent_by_score_bound(evaluation) is True
    assert status_updates == [(agent_id, AgentStatus.evaluating, AgentStatus.cancelled)]


@pytest.mark.anyio
async def test_score_bound_pruning_allows_ties(monkeypatch) -> None:
    agent_id = uuid4()
    evaluation = _evaluation(agent_id)
    status_updates: list = []

    async def fake_get_agent_by_id(_agent_id):
        return _agent(_agent_id)

    async def fake_get_leader_score(_set_id, _excluded_agent_id, _required_validator_count):
        return 0.5

    async def fake_get_upper_bound(_evaluation_id):
        return LocalEvaluationScoreBound(total_runs=20, impossible_runs=10, upper_bound=0.5)

    async def fake_transition_agent_status_if_matches(_agent_id, expected_status, new_status):
        status_updates.append((_agent_id, expected_status, new_status))
        return True

    monkeypatch.setattr(validator_endpoint, "get_agent_by_id", fake_get_agent_by_id)
    monkeypatch.setattr(validator_endpoint, "get_approved_validator_leader_score_for_set", fake_get_leader_score)
    monkeypatch.setattr(validator_endpoint, "get_local_evaluation_score_upper_bound", fake_get_upper_bound)
    monkeypatch.setattr(
        validator_endpoint, "transition_agent_status_if_matches", fake_transition_agent_status_if_matches
    )

    assert await validator_endpoint._maybe_stop_agent_by_score_bound(evaluation) is False
    assert status_updates == []


@pytest.mark.anyio
async def test_score_bound_pruning_fails_screener_1_when_upper_bound_is_below_threshold(monkeypatch) -> None:
    agent_id = uuid4()
    evaluation = _evaluation(agent_id, group=EvaluationSetGroup.screener_1)
    status_updates: list = []

    async def fake_get_agent_by_id(_agent_id):
        return _agent(_agent_id, status=AgentStatus.screening_1)

    async def fake_get_upper_bound(_evaluation_id):
        return LocalEvaluationScoreBound(total_runs=20, impossible_runs=10, upper_bound=0.5)

    async def fake_transition_agent_status_if_matches(_agent_id, expected_status, new_status):
        status_updates.append((_agent_id, expected_status, new_status))
        return True

    monkeypatch.setattr(validator_endpoint.config, "SCREENER_1_THRESHOLD", 0.6)
    monkeypatch.setattr(validator_endpoint, "get_agent_by_id", fake_get_agent_by_id)
    monkeypatch.setattr(validator_endpoint, "get_local_evaluation_score_upper_bound", fake_get_upper_bound)
    monkeypatch.setattr(
        validator_endpoint, "transition_agent_status_if_matches", fake_transition_agent_status_if_matches
    )

    assert await validator_endpoint._maybe_stop_agent_by_score_bound(evaluation) is True
    assert status_updates == [(agent_id, AgentStatus.screening_1, AgentStatus.failed_screening_1)]


@pytest.mark.anyio
async def test_score_bound_pruning_screener_2_uses_screener_threshold(monkeypatch) -> None:
    agent_id = uuid4()
    evaluation = _evaluation(agent_id, group=EvaluationSetGroup.screener_2)
    status_updates: list = []

    async def fake_get_agent_by_id(_agent_id):
        return _agent(_agent_id, status=AgentStatus.screening_2)

    async def fake_get_upper_bound(_evaluation_id):
        return LocalEvaluationScoreBound(total_runs=20, impossible_runs=10, upper_bound=0.5)

    async def fake_transition_agent_status_if_matches(_agent_id, expected_status, new_status):
        status_updates.append((_agent_id, expected_status, new_status))
        return True

    async def fail_if_top_agents_called(*_args, **_kwargs):
        raise AssertionError("score-bound pruning should use the screener 2 threshold directly")

    monkeypatch.setattr(validator_endpoint.config, "SCREENER_2_THRESHOLD", 0.6)
    monkeypatch.setattr(validator_endpoint, "get_agent_by_id", fake_get_agent_by_id)
    monkeypatch.setattr(validator_endpoint, "get_local_evaluation_score_upper_bound", fake_get_upper_bound)
    monkeypatch.setattr(
        validator_endpoint, "transition_agent_status_if_matches", fake_transition_agent_status_if_matches
    )
    monkeypatch.setattr(validator_endpoint, "get_top_agents", fail_if_top_agents_called)

    assert await validator_endpoint._maybe_stop_agent_by_score_bound(evaluation) is True
    assert status_updates == [(agent_id, AgentStatus.screening_2, AgentStatus.failed_screening_2)]


@pytest.mark.anyio
async def test_check_cancellation_returns_current_agent_status(monkeypatch) -> None:
    agent_id = uuid4()
    evaluation = _evaluation(agent_id)
    validator = _validator(evaluation, _agent(agent_id))

    async def fake_get_agent_by_id(_agent_id):
        return _agent(_agent_id, status=AgentStatus.cancelled)

    monkeypatch.setattr(validator_endpoint, "get_agent_by_id", fake_get_agent_by_id)

    response = await validator_endpoint.validator_check_cancellation(
        ValidatorCheckCancellationRequest(evaluation_id=evaluation.evaluation_id, agent_id=agent_id),
        validator=validator,
    )

    assert response.should_cancel is True
    assert response.reason is not None


@pytest.mark.anyio
async def test_check_cancellation_returns_true_for_failed_screener_agent(monkeypatch) -> None:
    agent_id = uuid4()
    evaluation = _evaluation(agent_id, group=EvaluationSetGroup.screener_2)
    validator = _validator(evaluation, _agent(agent_id, status=AgentStatus.screening_2))

    async def fake_get_agent_by_id(_agent_id):
        return _agent(_agent_id, status=AgentStatus.failed_screening_2)

    monkeypatch.setattr(validator_endpoint, "get_agent_by_id", fake_get_agent_by_id)

    response = await validator_endpoint.validator_check_cancellation(
        ValidatorCheckCancellationRequest(evaluation_id=evaluation.evaluation_id, agent_id=agent_id),
        validator=validator,
    )

    assert response.should_cancel is True
    assert response.reason is not None


@pytest.mark.anyio
async def test_check_cancellation_rejects_mismatched_agent() -> None:
    agent_id = uuid4()
    evaluation = _evaluation(agent_id)
    validator = _validator(evaluation, _agent(agent_id))

    with pytest.raises(HTTPException) as exc_info:
        await validator_endpoint.validator_check_cancellation(
            ValidatorCheckCancellationRequest(evaluation_id=evaluation.evaluation_id, agent_id=uuid4()),
            validator=validator,
        )

    assert exc_info.value.status_code == 409


@pytest.mark.anyio
async def test_cancel_current_evaluation_marks_unfinished_runs_and_clears_validator_state(monkeypatch) -> None:
    agent_id = uuid4()
    evaluation = _evaluation(agent_id)
    validator = _validator(evaluation, _agent(agent_id))
    calls: list[tuple[str, object]] = []

    async def fake_get_agent_by_id(_agent_id):
        return _agent(_agent_id, status=AgentStatus.cancelled)

    async def fake_set_unfinished_evaluation_runs_to_score_pruned(_evaluation_id, reason):
        calls.append(("mark_pruned", _evaluation_id, reason))
        return 3

    async def fake_handle_evaluation_if_finished(_evaluation_id):
        calls.append(("handle_finished", _evaluation_id))

    monkeypatch.setattr(validator_endpoint, "get_agent_by_id", fake_get_agent_by_id)
    monkeypatch.setattr(
        validator_endpoint,
        "set_unfinished_evaluation_runs_to_score_pruned",
        fake_set_unfinished_evaluation_runs_to_score_pruned,
    )
    monkeypatch.setattr(validator_endpoint, "handle_evaluation_if_finished", fake_handle_evaluation_if_finished)

    await validator_endpoint.validator_cancel_current_evaluation(
        ValidatorCancelCurrentEvaluationRequest(
            evaluation_id=evaluation.evaluation_id,
            agent_id=agent_id,
            reason="pruned",
        ),
        validator=validator,
    )

    assert calls == [
        ("mark_pruned", evaluation.evaluation_id, "pruned"),
        ("handle_finished", evaluation.evaluation_id),
    ]
    assert validator.current_evaluation_id is None
    assert validator.current_evaluation is None
    assert validator.current_agent is None


@pytest.mark.anyio
async def test_cancel_current_evaluation_accepts_failed_screener_agent(monkeypatch) -> None:
    agent_id = uuid4()
    evaluation = _evaluation(agent_id, group=EvaluationSetGroup.screener_1)
    validator = _validator(evaluation, _agent(agent_id, status=AgentStatus.screening_1))
    calls: list[tuple[str, object]] = []

    async def fake_get_agent_by_id(_agent_id):
        return _agent(_agent_id, status=AgentStatus.failed_screening_1)

    async def fake_set_unfinished_evaluation_runs_to_score_pruned(_evaluation_id, reason):
        calls.append(("mark_pruned", _evaluation_id, reason))
        return 3

    async def fake_handle_evaluation_if_finished(_evaluation_id):
        calls.append(("handle_finished", _evaluation_id))

    monkeypatch.setattr(validator_endpoint, "get_agent_by_id", fake_get_agent_by_id)
    monkeypatch.setattr(
        validator_endpoint,
        "set_unfinished_evaluation_runs_to_score_pruned",
        fake_set_unfinished_evaluation_runs_to_score_pruned,
    )
    monkeypatch.setattr(validator_endpoint, "handle_evaluation_if_finished", fake_handle_evaluation_if_finished)

    await validator_endpoint.validator_cancel_current_evaluation(
        ValidatorCancelCurrentEvaluationRequest(
            evaluation_id=evaluation.evaluation_id,
            agent_id=agent_id,
            reason="pruned",
        ),
        validator=validator,
    )

    assert calls == [
        ("mark_pruned", evaluation.evaluation_id, "pruned"),
        ("handle_finished", evaluation.evaluation_id),
    ]
    assert validator.current_evaluation_id is None
    assert validator.current_evaluation is None
    assert validator.current_agent is None


@pytest.mark.anyio
async def test_update_evaluation_run_ignores_stale_update_for_closed_score_stopped_evaluation(monkeypatch) -> None:
    agent_id = uuid4()
    evaluation = _evaluation(
        agent_id,
        group=EvaluationSetGroup.screener_1,
        finished_at=datetime.now(timezone.utc),
    )
    evaluation_run = EvaluationRun(
        evaluation_run_id=uuid4(),
        evaluation_id=evaluation.evaluation_id,
        problem_name="stale-problem",
        status=EvaluationRunStatus.pending,
        created_at=datetime.now(timezone.utc),
    )
    validator = validator_endpoint.Validator(
        session_id=uuid4(),
        name="validator",
        hotkey=evaluation.validator_hotkey,
        time_connected=datetime.now(timezone.utc),
        ip_address="127.0.0.1",
        current_evaluation_id=None,
        current_evaluation=None,
        current_agent=None,
    )

    async def fake_get_evaluation_run_by_id(_evaluation_run_id):
        return evaluation_run

    async def fake_get_evaluation_by_id(_evaluation_id):
        return evaluation

    async def fake_get_agent_by_id(_agent_id):
        return _agent(_agent_id, status=AgentStatus.failed_screening_1)

    async def fail_if_update_called(_evaluation_run):
        raise AssertionError("stale score-stopped updates must be ignored without mutating the run")

    monkeypatch.setattr(validator_endpoint, "get_evaluation_run_by_id", fake_get_evaluation_run_by_id)
    monkeypatch.setattr(validator_endpoint, "get_evaluation_by_id", fake_get_evaluation_by_id)
    monkeypatch.setattr(validator_endpoint, "get_agent_by_id", fake_get_agent_by_id)
    monkeypatch.setattr(validator_endpoint, "update_evaluation_run_by_id", fail_if_update_called)

    response = await validator_endpoint.validator_update_evaluation_run.__wrapped__(
        ValidatorUpdateEvaluationRunRequest(
            evaluation_run_id=evaluation_run.evaluation_run_id,
            updated_status=EvaluationRunStatus.initializing_agent,
        ),
        validator=validator,
    )

    assert response == validator_endpoint.ValidatorUpdateEvaluationRunResponse()


class _FakeBoundConn:
    def __init__(self):
        self.query: str | None = None

    async def fetchrow(self, query: str, *_args):
        self.query = query
        return {"total_runs": 20, "impossible_runs": 10}


@pytest.mark.anyio
async def test_local_upper_bound_only_reduces_for_unsolved_or_agent_errors() -> None:
    conn = _FakeBoundConn()

    score_bound = await get_local_evaluation_score_upper_bound.__wrapped__(conn, uuid4())

    assert score_bound == LocalEvaluationScoreBound(total_runs=20, impossible_runs=10, upper_bound=0.5)
    assert conn.query is not None
    assert "solved IS NOT TRUE" in conn.query
    assert "error_code >= 1000" in conn.query
    assert "error_code < 2000" in conn.query
