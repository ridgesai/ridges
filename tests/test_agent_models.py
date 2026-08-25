from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from models.agent import (
    Agent,
    AgentCompetitionStatus,
    AgentCreate,
    AgentStatus,
    ApprovalReviewStatus,
    PublicAgent,
    build_agent_competition_state,
    derive_agent_competition_status,
)


def test_agent_create_does_not_accept_pipeline_status() -> None:
    with pytest.raises(ValidationError, match="status"):
        AgentCreate(
            miner_hotkey="miner-hotkey",
            name="Agent",
            version_num=1,
            status=AgentStatus.screening_1,
            created_at=datetime.now(timezone.utc),
            payment_block_hash="block",
            payment_extrinsic_index="0",
        )


@pytest.mark.parametrize("status", [status for status in AgentStatus if status is not AgentStatus.finished])
def test_non_finished_competition_status_matches_pipeline_status(status: AgentStatus):
    assert (
        derive_agent_competition_status(
            status=status,
            set_id=24,
            approved=True,
            approval_review_status=ApprovalReviewStatus.rejected,
        ).value
        == status.value
    )


def test_competition_status_enum_contains_every_pipeline_status():
    assert {status.value for status in AgentStatus} <= {status.value for status in AgentCompetitionStatus}


def test_core_agent_does_not_expose_competition_payload():
    agent = Agent(
        agent_id=uuid4(),
        miner_hotkey="miner-hotkey",
        name="Agent",
        version_num=1,
        status=AgentStatus.screening_1,
        created_at=datetime.now(timezone.utc),
    )

    assert (
        {
            "set_id",
            "approved",
            "performance_delta",
            "cost_delta",
            "relative_improvement_units",
            "time_multiplier",
            "initial_reward_score",
            "approved_at",
            "baseline_agent_id",
            "baseline_agent_name",
            "baseline_agent_version_num",
            "competition_state",
        }
        - {"set_id"}
    ).isdisjoint(agent.model_dump())
    assert agent.model_dump()["set_id"] is None


def test_rejection_takes_precedence_over_published_approval():
    assert (
        derive_agent_competition_status(
            status=AgentStatus.finished,
            set_id=24,
            approved=True,
            approval_review_status=ApprovalReviewStatus.rejected,
            relative_improvement_units=1.5,
        )
        is AgentCompetitionStatus.rejected
    )
    assert (
        build_agent_competition_state(
            status=AgentStatus.finished,
            set_id=24,
            approved=True,
            approval_review_status=ApprovalReviewStatus.rejected,
            relative_improvement_units=1.5,
        ).approved
        is False
    )


@pytest.mark.parametrize(
    "review_status",
    [
        ApprovalReviewStatus.pending,
        ApprovalReviewStatus.processing,
        ApprovalReviewStatus.under_review,
    ],
)
def test_published_approval_remains_effective_during_in_progress_review(review_status):
    state = build_agent_competition_state(
        status=AgentStatus.finished,
        set_id=24,
        approved=True,
        approval_review_status=review_status,
        relative_improvement_units=1.5,
    )

    assert state.status is AgentCompetitionStatus.baseline
    assert state.approved is True


@pytest.mark.parametrize(
    "review_status",
    [
        ApprovalReviewStatus.pending,
        ApprovalReviewStatus.processing,
        ApprovalReviewStatus.under_review,
    ],
)
def test_in_progress_review_without_published_approval_is_under_review(review_status):
    state = build_agent_competition_state(
        status=AgentStatus.finished,
        set_id=24,
        approved=False,
        approval_review_status=review_status,
    )

    assert state.status is AgentCompetitionStatus.under_review
    assert state.approved is False


def test_finished_approved_agent_is_identified_as_baseline_only_with_baseline_shape():
    assert (
        derive_agent_competition_status(
            status=AgentStatus.finished,
            set_id=24,
            approved=True,
            relative_improvement_units=1.5,
        )
        is AgentCompetitionStatus.baseline
    )
    assert (
        derive_agent_competition_status(
            status=AgentStatus.finished,
            set_id=24,
            approved=True,
            performance_delta=0.1,
            relative_improvement_units=1.5,
        )
        is AgentCompetitionStatus.approved
    )
    assert (
        derive_agent_competition_status(
            status=AgentStatus.finished,
            set_id=24,
            approved=True,
        )
        is AgentCompetitionStatus.approved
    )


def test_approved_review_is_authoritative_if_approval_projection_lags():
    assert (
        derive_agent_competition_status(
            status=AgentStatus.finished,
            set_id=24,
            approved=False,
            approval_review_status=ApprovalReviewStatus.approved,
        )
        is AgentCompetitionStatus.approved
    )
    assert (
        build_agent_competition_state(
            status=AgentStatus.finished,
            set_id=24,
            approved=False,
            approval_review_status=ApprovalReviewStatus.approved,
        ).approved
        is True
    )


def test_finished_without_competition_context_is_not_mislabeled():
    assert (
        derive_agent_competition_status(status=AgentStatus.finished, approved=False) is AgentCompetitionStatus.finished
    )
    assert derive_agent_competition_status(status=AgentStatus.finished, set_id=24) is AgentCompetitionStatus.finished
    assert (
        derive_agent_competition_status(status=AgentStatus.finished, set_id=24, approved=False)
        is AgentCompetitionStatus.didnt_qualify
    )


def test_public_agent_populates_complete_competition_state():
    baseline_id = uuid4()
    approved_at = datetime.now(timezone.utc)
    agent = PublicAgent(
        agent_id=uuid4(),
        miner_hotkey="miner-hotkey",
        name="Agent",
        version_num=3,
        status=AgentStatus.finished,
        created_at=datetime.now(timezone.utc),
        set_id=24,
        approved=True,
        approval_review_status=ApprovalReviewStatus.approved,
        performance_delta=0.12,
        cost_delta=0.08,
        relative_improvement_units=1.6,
        time_multiplier=1.2,
        initial_reward_score=1.92,
        approved_at=approved_at,
        baseline_agent_id=baseline_id,
        baseline_agent_name="Baseline",
        baseline_agent_version_num=2,
        rank=2,
        final_score=0.5,
        validator_count=3,
        validator_hotkeys=["validator-a", "validator-b", "validator-c"],
        average_cost_usd=0.03,
        average_runtime_seconds=12.5,
        disqualified=False,
        emission=0.25,
        reward_weight=0.75,
    )

    assert agent.competition_state is not None
    assert agent.competition_state.status is AgentCompetitionStatus.approved
    assert agent.competition_state.approved is True
    assert agent.competition_state.set_id == 24
    assert agent.competition_state.approved_at == approved_at
    assert agent.competition_state.baseline_agent_id == baseline_id
    assert agent.competition_state.baseline_agent_name == "Baseline"
    assert agent.competition_state.baseline_agent_version_num == 2
    assert agent.competition_state.rank == 2
    assert agent.competition_state.final_score == 0.5
    assert agent.emission == 0.25
    assert agent.reward_weight == 0.75
    assert agent.legacy_membership is False
    assert agent.status is AgentStatus.finished
    serialized = agent.model_dump()
    assert serialized["id"] == agent.agent_id
    assert serialized["set_id"] == 24
    assert serialized["approved"] is True
    assert serialized["approval_review_status"] is ApprovalReviewStatus.approved
    assert serialized["rank"] == 2
    assert serialized["final_score"] == 0.5
    assert serialized["validator_count"] == 3
    assert serialized["validator_hotkeys"] == ["validator-a", "validator-b", "validator-c"]
    assert serialized["average_cost_usd"] == 0.03
    assert serialized["average_runtime_seconds"] == 12.5
    assert serialized["disqualified"] is False
    assert serialized["performance_delta"] == 0.12
    assert serialized["cost_delta"] == 0.08
    assert serialized["relative_improvement_units"] == 1.6
    assert serialized["time_multiplier"] == 1.2
    assert serialized["initial_reward_score"] == 1.92
    assert serialized["approved_at"] == approved_at
    assert serialized["baseline_agent_id"] == baseline_id
    assert serialized["baseline_agent_name"] == "Baseline"
    assert serialized["baseline_agent_version_num"] == 2
    assert set(serialized) == {
        "agent_id",
        "miner_hotkey",
        "name",
        "version_num",
        "status",
        "created_at",
        "legacy_membership",
        "emission",
        "reward_weight",
        "competition_state",
        "id",
        "set_id",
        "approved",
        "approval_review_status",
        "rank",
        "final_score",
        "validator_count",
        "validator_hotkeys",
        "average_cost_usd",
        "average_runtime_seconds",
        "disqualified",
        "approved_at",
        "performance_delta",
        "cost_delta",
        "relative_improvement_units",
        "time_multiplier",
        "initial_reward_score",
        "baseline_agent_id",
        "baseline_agent_name",
        "baseline_agent_version_num",
    }


def test_public_agent_recomputes_supplied_competition_state():
    agent = PublicAgent(
        agent_id=uuid4(),
        miner_hotkey="miner-hotkey",
        name="Agent",
        version_num=1,
        status=AgentStatus.evaluating,
        created_at=datetime.now(timezone.utc),
        set_id=24,
        approved=True,
        competition_state={"status": "approved", "approved": True},
    )

    assert agent.competition_state.status is AgentCompetitionStatus.evaluating
    assert agent.competition_state.approved is False


def test_public_agent_without_competition_context_has_null_state():
    agent = PublicAgent(
        agent_id=uuid4(),
        miner_hotkey="miner-hotkey",
        name="Agent",
        version_num=1,
        status=AgentStatus.screening_1,
        created_at=datetime.now(timezone.utc),
    )

    assert agent.competition_state is None
    assert agent.id == agent.agent_id
    assert agent.set_id is None
    assert agent.approved is None
    assert agent.final_score is None
