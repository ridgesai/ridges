from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from api.endpoints import validator as validator_endpoint
from models.agent import Agent, AgentStatus
from models.evaluation import EvaluationStatus, HydratedEvaluation
from models.evaluation_set import EvaluationSetGroup
from queries.evaluation import AgentRankingProfile


@pytest.fixture(autouse=True)
def default_candidate_is_not_banned(monkeypatch):
    async def not_banned(_agent_id) -> bool:
        return False

    monkeypatch.setattr(validator_endpoint, "is_agent_coldkey_banned", not_banned)


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

    async def fake_transition_agent_status_if_matches(*_args, **_kwargs) -> bool:
        raise AssertionError("transition_agent_status_if_matches should not be called when auto approval is enabled")

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
    monkeypatch.setattr(
        validator_endpoint, "transition_agent_status_if_matches", fake_transition_agent_status_if_matches
    )
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

    async def fake_transition_agent_status_if_matches(_agent_id, expected_status, new_status) -> bool:
        recorded["agent_id"] = _agent_id
        recorded["expected_status"] = expected_status
        recorded["status"] = new_status
        return True

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
    monkeypatch.setattr(
        validator_endpoint, "transition_agent_status_if_matches", fake_transition_agent_status_if_matches
    )
    monkeypatch.setattr(validator_endpoint.config, "AUTO_APPROVAL_ENABLED", True)

    await validator_endpoint.handle_evaluation_if_finished(uuid4())

    assert recorded == {
        "approval_candidate_agent_id": agent_id,
        "approval_candidate_set_id": 11,
        "agent_id": agent_id,
        "expected_status": AgentStatus.evaluating,
        "status": AgentStatus.finished,
    }


@pytest.mark.anyio
async def test_should_run_auto_approval_judge_when_no_approved_leader(monkeypatch) -> None:
    agent_id = uuid4()
    now = datetime.now(timezone.utc)

    async def fake_get_validator_agent_score_for_set(_agent_id, _set_id, _required_validator_count):
        return AgentRankingProfile(final_score=0.61, avg_cost_usd=0.05, created_at=now)

    async def fake_get_approved_leader_ranking_for_set(_set_id, _excluded_agent_id, _required_validator_count):
        return None

    monkeypatch.setattr(validator_endpoint, "get_validator_agent_score_for_set", fake_get_validator_agent_score_for_set)
    monkeypatch.setattr(
        validator_endpoint,
        "get_approved_leader_ranking_for_set",
        fake_get_approved_leader_ranking_for_set,
    )

    assert await validator_endpoint._should_run_auto_approval_judge(agent_id=agent_id, set_id=11) is True


@pytest.mark.anyio
async def test_should_run_auto_approval_judge_rejects_banned_candidate_before_scoring(monkeypatch) -> None:
    agent_id = uuid4()

    async def is_banned(_agent_id) -> bool:
        assert _agent_id == agent_id
        return True

    async def unexpected_score_lookup(*_args):
        raise AssertionError("A banned candidate should be rejected before score lookup")

    monkeypatch.setattr(validator_endpoint, "is_agent_coldkey_banned", is_banned)
    monkeypatch.setattr(validator_endpoint, "get_validator_agent_score_for_set", unexpected_score_lookup)

    assert await validator_endpoint._should_run_auto_approval_judge(agent_id=agent_id, set_id=11) is False


@pytest.mark.anyio
async def test_should_run_auto_approval_judge_rejects_full_tie_with_leader(monkeypatch) -> None:
    agent_id = uuid4()
    now = datetime.now(timezone.utc)
    profile = AgentRankingProfile(final_score=0.75, avg_cost_usd=0.05, created_at=now)

    async def fake_get_validator_agent_score_for_set(_agent_id, _set_id, _required_validator_count):
        return profile

    async def fake_get_approved_leader_ranking_for_set(_set_id, _excluded_agent_id, _required_validator_count):
        return profile

    monkeypatch.setattr(validator_endpoint, "get_validator_agent_score_for_set", fake_get_validator_agent_score_for_set)
    monkeypatch.setattr(
        validator_endpoint,
        "get_approved_leader_ranking_for_set",
        fake_get_approved_leader_ranking_for_set,
    )

    assert await validator_endpoint._should_run_auto_approval_judge(agent_id=agent_id, set_id=11) is False


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

    async def fake_transition_agent_status_if_matches(_agent_id, expected_status, new_status) -> bool:
        recorded["agent_id"] = _agent_id
        recorded["expected_status"] = expected_status
        recorded["status"] = new_status
        return True

    monkeypatch.setattr(validator_endpoint, "update_evaluation_finished_at", fake_update_evaluation_finished_at)
    monkeypatch.setattr(validator_endpoint, "get_hydrated_evaluation_by_id", fake_get_hydrated_evaluation_by_id)
    monkeypatch.setattr(validator_endpoint, "get_agent_by_id", fake_get_agent_by_id)
    monkeypatch.setattr(
        validator_endpoint,
        "get_num_successful_validator_evaluations_for_agent_id",
        fake_get_num_successful_validator_evaluations_for_agent_id,
    )
    monkeypatch.setattr(validator_endpoint, "finish_agent_and_enqueue_approval", fake_finish_agent_and_enqueue_approval)
    monkeypatch.setattr(
        validator_endpoint, "transition_agent_status_if_matches", fake_transition_agent_status_if_matches
    )
    monkeypatch.setattr(validator_endpoint.config, "AUTO_APPROVAL_ENABLED", False)

    await validator_endpoint.handle_evaluation_if_finished(uuid4())

    assert recorded == {
        "agent_id": agent_id,
        "expected_status": AgentStatus.evaluating,
        "status": AgentStatus.finished,
    }


@pytest.mark.anyio
async def test_should_run_auto_approval_judge_accepts_candidate_with_lower_cost(monkeypatch) -> None:
    agent_id = uuid4()
    now = datetime.now(timezone.utc)

    async def fake_get_validator_agent_score_for_set(_agent_id, _set_id, _required_validator_count):
        return AgentRankingProfile(final_score=0.75, avg_cost_usd=0.03, created_at=now)

    async def fake_get_approved_leader_ranking_for_set(_set_id, _excluded_agent_id, _required_validator_count):
        return AgentRankingProfile(final_score=0.75, avg_cost_usd=0.05, created_at=now)

    monkeypatch.setattr(validator_endpoint, "get_validator_agent_score_for_set", fake_get_validator_agent_score_for_set)
    monkeypatch.setattr(
        validator_endpoint,
        "get_approved_leader_ranking_for_set",
        fake_get_approved_leader_ranking_for_set,
    )

    assert await validator_endpoint._should_run_auto_approval_judge(agent_id=agent_id, set_id=11) is True


@pytest.mark.anyio
async def test_should_run_auto_approval_judge_rejects_candidate_with_higher_cost(monkeypatch) -> None:
    agent_id = uuid4()
    now = datetime.now(timezone.utc)

    async def fake_get_validator_agent_score_for_set(_agent_id, _set_id, _required_validator_count):
        return AgentRankingProfile(final_score=0.75, avg_cost_usd=0.08, created_at=now)

    async def fake_get_approved_leader_ranking_for_set(_set_id, _excluded_agent_id, _required_validator_count):
        return AgentRankingProfile(final_score=0.75, avg_cost_usd=0.05, created_at=now)

    monkeypatch.setattr(validator_endpoint, "get_validator_agent_score_for_set", fake_get_validator_agent_score_for_set)
    monkeypatch.setattr(
        validator_endpoint,
        "get_approved_leader_ranking_for_set",
        fake_get_approved_leader_ranking_for_set,
    )

    assert await validator_endpoint._should_run_auto_approval_judge(agent_id=agent_id, set_id=11) is False


@pytest.mark.anyio
async def test_should_run_auto_approval_judge_rejects_newer_candidate_same_score_and_cost(monkeypatch) -> None:
    agent_id = uuid4()
    now = datetime.now(timezone.utc)
    earlier = now - timedelta(hours=1)

    async def fake_get_validator_agent_score_for_set(_agent_id, _set_id, _required_validator_count):
        return AgentRankingProfile(final_score=0.75, avg_cost_usd=0.05, created_at=now)

    async def fake_get_approved_leader_ranking_for_set(_set_id, _excluded_agent_id, _required_validator_count):
        return AgentRankingProfile(final_score=0.75, avg_cost_usd=0.05, created_at=earlier)

    monkeypatch.setattr(validator_endpoint, "get_validator_agent_score_for_set", fake_get_validator_agent_score_for_set)
    monkeypatch.setattr(
        validator_endpoint,
        "get_approved_leader_ranking_for_set",
        fake_get_approved_leader_ranking_for_set,
    )

    assert await validator_endpoint._should_run_auto_approval_judge(agent_id=agent_id, set_id=11) is False


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("candidate_score", "candidate_cost", "expected"),
    [
        (0.515, 0.20, True),  # Exactly 3% better performance.
        (0.50, 0.094, True),  # Same performance and exactly 6% lower cost.
        (0.49, 0.05, False),  # A lower score cannot qualify through cost alone.
        (0.514, 0.096, False),  # Neither relative threshold is met.
    ],
)
async def test_active_incentive_prefilter_uses_relative_thresholds(
    monkeypatch,
    candidate_score: float,
    candidate_cost: float,
    expected: bool,
) -> None:
    agent_id = uuid4()
    now = datetime.now(timezone.utc)

    async def fake_get_validator_agent_score_for_set(_agent_id, _set_id, _required_validator_count):
        return AgentRankingProfile(final_score=candidate_score, avg_cost_usd=candidate_cost, created_at=now)

    async def fake_get_approved_leader_ranking_for_set(_set_id, _excluded_agent_id, _required_validator_count):
        return AgentRankingProfile(final_score=0.50, avg_cost_usd=0.10, created_at=now)

    monkeypatch.setattr(validator_endpoint.config, "INCENTIVE_START_SET_ID", 11)
    monkeypatch.setattr(validator_endpoint, "get_validator_agent_score_for_set", fake_get_validator_agent_score_for_set)
    monkeypatch.setattr(
        validator_endpoint,
        "get_approved_leader_ranking_for_set",
        fake_get_approved_leader_ranking_for_set,
    )

    assert await validator_endpoint._should_run_auto_approval_judge(agent_id=agent_id, set_id=11) is expected
