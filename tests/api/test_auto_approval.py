from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from api.endpoints import validator as validator_endpoint
from models.agent import Agent, AgentStatus
from models.evaluation import EvaluationStatus, HydratedEvaluation
from models.evaluation_set import EvaluationSetGroup


def _hydrated_evaluation(*, agent_id, set_id: int = 7) -> HydratedEvaluation:
    return HydratedEvaluation(
        evaluation_id=uuid4(),
        agent_id=agent_id,
        validator_hotkey="validator-hotkey",
        set_id=set_id,
        evaluation_set_group=EvaluationSetGroup.validator,
        created_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        status=EvaluationStatus.success,
        score=0.83,
    )


def _agent(*, agent_id, status: AgentStatus) -> Agent:
    return Agent(
        agent_id=agent_id,
        miner_hotkey="miner-hotkey",
        name="Agent",
        version_num=0,
        status=status,
        created_at=datetime.now(timezone.utc),
        ip_address="127.0.0.1",
    )


@pytest.mark.anyio
async def test_handle_evaluation_finished_enqueues_auto_approval(monkeypatch) -> None:
    agent_id = uuid4()
    hydrated = _hydrated_evaluation(agent_id=agent_id, set_id=11)
    recorded: dict[str, object] = {}

    async def fake_update_evaluation_finished_at(_evaluation_id) -> None:
        return None

    async def fake_get_hydrated_evaluation_by_id(_evaluation_id):
        return hydrated

    async def fake_get_agent_by_id(_agent_id):
        return _agent(agent_id=agent_id, status=AgentStatus.evaluating)

    async def fake_get_num_successful_validator_evaluations_for_agent_id(_agent_id):
        return validator_endpoint.config.NUM_EVALS_PER_AGENT

    async def fake_finish_agent_and_enqueue_approval(*, agent_id, set_id, policy_version):
        recorded["agent_id"] = agent_id
        recorded["set_id"] = set_id
        recorded["policy_version"] = policy_version
        return True

    async def fake_should_run_auto_approval_judge(*, agent_id, set_id):
        recorded["approval_candidate_agent_id"] = agent_id
        recorded["approval_candidate_set_id"] = set_id
        return True

    async def fake_update_agent_status(*_args, **_kwargs) -> None:
        raise AssertionError("update_agent_status should not be called when auto approval is enabled")

    monkeypatch.setattr(validator_endpoint, "update_evaluation_finished_at", fake_update_evaluation_finished_at)
    monkeypatch.setattr(validator_endpoint, "get_hydrated_evaluation_by_id", fake_get_hydrated_evaluation_by_id)
    monkeypatch.setattr(validator_endpoint, "get_agent_by_id", fake_get_agent_by_id)
    monkeypatch.setattr(
        validator_endpoint,
        "get_num_successful_validator_evaluations_for_agent_id",
        fake_get_num_successful_validator_evaluations_for_agent_id,
    )
    monkeypatch.setattr(validator_endpoint, "finish_agent_and_enqueue_approval", fake_finish_agent_and_enqueue_approval)
    monkeypatch.setattr(validator_endpoint, "_should_run_auto_approval_judge", fake_should_run_auto_approval_judge)
    monkeypatch.setattr(validator_endpoint, "update_agent_status", fake_update_agent_status)
    monkeypatch.setattr(validator_endpoint.config, "AUTO_APPROVAL_ENABLED", True)
    monkeypatch.setattr(validator_endpoint.config, "AUTO_APPROVAL_POLICY_VERSION", "approval-v1")

    await validator_endpoint.handle_evaluation_if_finished(uuid4())

    assert recorded == {
        "approval_candidate_agent_id": agent_id,
        "approval_candidate_set_id": 11,
        "agent_id": agent_id,
        "set_id": 11,
        "policy_version": "approval-v1",
    }


@pytest.mark.anyio
async def test_handle_evaluation_finished_skips_auto_approval_for_non_leader_candidate(monkeypatch) -> None:
    agent_id = uuid4()
    hydrated = _hydrated_evaluation(agent_id=agent_id, set_id=11)
    recorded: dict[str, object] = {}

    async def fake_update_evaluation_finished_at(_evaluation_id) -> None:
        return None

    async def fake_get_hydrated_evaluation_by_id(_evaluation_id):
        return hydrated

    async def fake_get_agent_by_id(_agent_id):
        return _agent(agent_id=agent_id, status=AgentStatus.evaluating)

    async def fake_get_num_successful_validator_evaluations_for_agent_id(_agent_id):
        return validator_endpoint.config.NUM_EVALS_PER_AGENT

    async def fake_should_run_auto_approval_judge(*, agent_id, set_id):
        recorded["approval_candidate_agent_id"] = agent_id
        recorded["approval_candidate_set_id"] = set_id
        return False

    async def fake_finish_agent_and_enqueue_approval(**_kwargs):
        raise AssertionError("finish_agent_and_enqueue_approval should not be called for non-leader candidates")

    async def fake_update_agent_status(_agent_id, new_status) -> None:
        recorded["agent_id"] = _agent_id
        recorded["status"] = new_status

    monkeypatch.setattr(validator_endpoint, "update_evaluation_finished_at", fake_update_evaluation_finished_at)
    monkeypatch.setattr(validator_endpoint, "get_hydrated_evaluation_by_id", fake_get_hydrated_evaluation_by_id)
    monkeypatch.setattr(validator_endpoint, "get_agent_by_id", fake_get_agent_by_id)
    monkeypatch.setattr(
        validator_endpoint,
        "get_num_successful_validator_evaluations_for_agent_id",
        fake_get_num_successful_validator_evaluations_for_agent_id,
    )
    monkeypatch.setattr(validator_endpoint, "_should_run_auto_approval_judge", fake_should_run_auto_approval_judge)
    monkeypatch.setattr(validator_endpoint, "finish_agent_and_enqueue_approval", fake_finish_agent_and_enqueue_approval)
    monkeypatch.setattr(validator_endpoint, "update_agent_status", fake_update_agent_status)
    monkeypatch.setattr(validator_endpoint.config, "AUTO_APPROVAL_ENABLED", True)

    await validator_endpoint.handle_evaluation_if_finished(uuid4())

    assert recorded == {
        "approval_candidate_agent_id": agent_id,
        "approval_candidate_set_id": 11,
        "agent_id": agent_id,
        "status": AgentStatus.finished,
    }


@pytest.mark.anyio
async def test_should_run_auto_approval_judge_when_no_approved_leader(monkeypatch) -> None:
    agent_id = uuid4()

    async def fake_get_validator_agent_score_for_set(_agent_id, _set_id, _required_validator_count):
        return 0.61

    async def fake_get_approved_validator_leader_score_for_set(_set_id, _excluded_agent_id, _required_validator_count):
        return None

    monkeypatch.setattr(validator_endpoint, "get_validator_agent_score_for_set", fake_get_validator_agent_score_for_set)
    monkeypatch.setattr(
        validator_endpoint,
        "get_approved_validator_leader_score_for_set",
        fake_get_approved_validator_leader_score_for_set,
    )

    assert await validator_endpoint._should_run_auto_approval_judge(agent_id=agent_id, set_id=11) is True


@pytest.mark.anyio
async def test_should_run_auto_approval_judge_allows_candidate_to_tie_approved_leader(monkeypatch) -> None:
    agent_id = uuid4()

    async def fake_get_validator_agent_score_for_set(_agent_id, _set_id, _required_validator_count):
        return 0.75

    async def fake_get_approved_validator_leader_score_for_set(_set_id, _excluded_agent_id, _required_validator_count):
        return 0.75

    monkeypatch.setattr(validator_endpoint, "get_validator_agent_score_for_set", fake_get_validator_agent_score_for_set)
    monkeypatch.setattr(
        validator_endpoint,
        "get_approved_validator_leader_score_for_set",
        fake_get_approved_validator_leader_score_for_set,
    )

    assert await validator_endpoint._should_run_auto_approval_judge(agent_id=agent_id, set_id=11) is True


@pytest.mark.anyio
async def test_should_run_auto_approval_judge_when_candidate_beats_approved_leader(monkeypatch) -> None:
    agent_id = uuid4()

    async def fake_get_validator_agent_score_for_set(_agent_id, _set_id, _required_validator_count):
        return 0.76

    async def fake_get_approved_validator_leader_score_for_set(_set_id, _excluded_agent_id, _required_validator_count):
        return 0.75

    monkeypatch.setattr(validator_endpoint, "get_validator_agent_score_for_set", fake_get_validator_agent_score_for_set)
    monkeypatch.setattr(
        validator_endpoint,
        "get_approved_validator_leader_score_for_set",
        fake_get_approved_validator_leader_score_for_set,
    )

    assert await validator_endpoint._should_run_auto_approval_judge(agent_id=agent_id, set_id=11) is True


@pytest.mark.anyio
async def test_handle_evaluation_finished_updates_status_without_auto_approval(monkeypatch) -> None:
    agent_id = uuid4()
    hydrated = _hydrated_evaluation(agent_id=agent_id, set_id=13)
    recorded: dict[str, object] = {}

    async def fake_update_evaluation_finished_at(_evaluation_id) -> None:
        return None

    async def fake_get_hydrated_evaluation_by_id(_evaluation_id):
        return hydrated

    async def fake_get_agent_by_id(_agent_id):
        return _agent(agent_id=agent_id, status=AgentStatus.evaluating)

    async def fake_get_num_successful_validator_evaluations_for_agent_id(_agent_id):
        return validator_endpoint.config.NUM_EVALS_PER_AGENT

    async def fake_finish_agent_and_enqueue_approval(**_kwargs):
        raise AssertionError("finish_agent_and_enqueue_approval should not be called when auto approval is disabled")

    async def fake_update_agent_status(_agent_id, new_status) -> None:
        recorded["agent_id"] = _agent_id
        recorded["status"] = new_status

    monkeypatch.setattr(validator_endpoint, "update_evaluation_finished_at", fake_update_evaluation_finished_at)
    monkeypatch.setattr(validator_endpoint, "get_hydrated_evaluation_by_id", fake_get_hydrated_evaluation_by_id)
    monkeypatch.setattr(validator_endpoint, "get_agent_by_id", fake_get_agent_by_id)
    monkeypatch.setattr(
        validator_endpoint,
        "get_num_successful_validator_evaluations_for_agent_id",
        fake_get_num_successful_validator_evaluations_for_agent_id,
    )
    monkeypatch.setattr(validator_endpoint, "finish_agent_and_enqueue_approval", fake_finish_agent_and_enqueue_approval)
    monkeypatch.setattr(validator_endpoint, "update_agent_status", fake_update_agent_status)
    monkeypatch.setattr(validator_endpoint.config, "AUTO_APPROVAL_ENABLED", False)

    await validator_endpoint.handle_evaluation_if_finished(uuid4())

    assert recorded == {
        "agent_id": agent_id,
        "status": AgentStatus.finished,
    }
