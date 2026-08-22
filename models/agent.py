from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, computed_field, model_validator


class AgentStatus(str, Enum):
    screening_1 = "screening_1"
    failed_screening_1 = "failed_screening_1"
    screening_2 = "screening_2"
    failed_screening_2 = "failed_screening_2"
    evaluating = "evaluating"
    finished = "finished"
    cancelled = "cancelled"
    pre_screening = "pre_screening"
    failed_pre_screening = "failed_pre_screening"
    pre_screening_needs_review = "pre_screening_needs_review"


class ApprovalReviewStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    under_review = "under_review"
    approved = "approved"
    rejected = "rejected"


class AgentCompetitionStatus(str, Enum):
    """Public agent status from the point of view of a competition"""

    pre_screening = "pre_screening"
    failed_pre_screening = "failed_pre_screening"
    pre_screening_needs_review = "pre_screening_needs_review"
    screening_1 = "screening_1"
    failed_screening_1 = "failed_screening_1"
    screening_2 = "screening_2"
    failed_screening_2 = "failed_screening_2"
    evaluating = "evaluating"
    finished = "finished"
    cancelled = "cancelled"
    rejected = "rejected"
    didnt_qualify = "didnt_qualify"
    under_review = "under_review"
    approved = "approved"
    baseline = "baseline"


def derive_agent_competition_status(
    *,
    status: AgentStatus | str,
    set_id: int | None = None,
    approved: bool | None = None,
    approval_review_status: ApprovalReviewStatus | str | None = None,
    performance_delta: float | None = None,
    cost_delta: float | None = None,
    relative_improvement_units: float | None = None,
    approved_at: datetime | None = None,
    baseline_agent_id: UUID | None = None,
) -> AgentCompetitionStatus:
    """Derive the single public competition status for an agent.

    A rejection invalidates an existing approval. Other in-progress review
    states do not revoke a published approval, so an approved agent remains
    approved while a later review is pending. Without any competition context,
    a raw ``finished`` status is preserved instead of guessing that the agent
    failed to qualify.
    """

    if isinstance(status, str):
        status = AgentStatus(status)
    if isinstance(approval_review_status, str):
        approval_review_status = ApprovalReviewStatus(approval_review_status)

    if status is not AgentStatus.finished:
        return AgentCompetitionStatus(status.value)

    if approval_review_status is ApprovalReviewStatus.rejected:
        return AgentCompetitionStatus.rejected

    is_approved = approved is True or approval_review_status is ApprovalReviewStatus.approved
    if is_approved:
        if performance_delta is None and cost_delta is None and relative_improvement_units is not None:
            return AgentCompetitionStatus.baseline
        return AgentCompetitionStatus.approved

    if approval_review_status in {
        ApprovalReviewStatus.pending,
        ApprovalReviewStatus.processing,
        ApprovalReviewStatus.under_review,
    }:
        return AgentCompetitionStatus.under_review

    has_competition_context = (set_id is not None and approved is not None) or any(
        value is not None
        for value in (
            approval_review_status,
            approved_at,
            performance_delta,
            cost_delta,
            relative_improvement_units,
            baseline_agent_id,
        )
    )
    return AgentCompetitionStatus.didnt_qualify if has_competition_context else AgentCompetitionStatus.finished


class AgentCompetitionState(BaseModel):
    set_id: int
    status: AgentCompetitionStatus
    approved: bool
    approval_review_status: ApprovalReviewStatus | None = None
    rank: int | None = None
    final_score: float | None = None
    validator_count: int | None = None
    validator_hotkeys: list[str] | None = None
    average_cost_usd: float | None = None
    average_runtime_seconds: float | None = None
    disqualified: bool | None = None
    approved_at: datetime | None = None
    performance_delta: float | None = None
    cost_delta: float | None = None
    relative_improvement_units: float | None = None
    time_multiplier: float | None = None
    initial_reward_score: float | None = None
    baseline_agent_id: UUID | None = None
    baseline_agent_name: str | None = None
    baseline_agent_version_num: int | None = None


def build_agent_competition_state(
    *,
    status: AgentStatus | str,
    set_id: int,
    approved: bool,
    approval_review_status: ApprovalReviewStatus | str | None = None,
    rank: int | None = None,
    final_score: float | None = None,
    validator_count: int | None = None,
    validator_hotkeys: list[str] | None = None,
    average_cost_usd: float | None = None,
    average_runtime_seconds: float | None = None,
    disqualified: bool | None = None,
    approved_at: datetime | None = None,
    performance_delta: float | None = None,
    cost_delta: float | None = None,
    relative_improvement_units: float | None = None,
    time_multiplier: float | None = None,
    initial_reward_score: float | None = None,
    baseline_agent_id: UUID | None = None,
    baseline_agent_name: str | None = None,
    baseline_agent_version_num: int | None = None,
) -> AgentCompetitionState:
    competition_status = derive_agent_competition_status(
        status=status,
        set_id=set_id,
        approved=approved,
        approval_review_status=approval_review_status,
        performance_delta=performance_delta,
        cost_delta=cost_delta,
        relative_improvement_units=relative_improvement_units,
        approved_at=approved_at,
        baseline_agent_id=baseline_agent_id,
    )
    return AgentCompetitionState(
        set_id=set_id,
        status=competition_status,
        approved=competition_status in {AgentCompetitionStatus.approved, AgentCompetitionStatus.baseline},
        approval_review_status=approval_review_status,
        rank=rank,
        final_score=final_score,
        validator_count=validator_count,
        validator_hotkeys=validator_hotkeys,
        average_cost_usd=average_cost_usd,
        average_runtime_seconds=average_runtime_seconds,
        disqualified=disqualified,
        approved_at=approved_at,
        performance_delta=performance_delta,
        cost_delta=cost_delta,
        relative_improvement_units=relative_improvement_units,
        time_multiplier=time_multiplier,
        initial_reward_score=initial_reward_score,
        baseline_agent_id=baseline_agent_id,
        baseline_agent_name=baseline_agent_name,
        baseline_agent_version_num=baseline_agent_version_num,
    )


class AgentBase(BaseModel):
    miner_hotkey: str

    name: str
    version_num: int

    created_at: datetime
    ip_address: Optional[str] = None


class Agent(AgentBase):
    """Core pipeline agent used by evaluation, queue, and upload machinery."""

    agent_id: UUID
    status: AgentStatus
    set_id: int | None = None
    approval_review_status: ApprovalReviewStatus | None = None


class PublicAgent(BaseModel):
    """The single agent shape returned by frontend-facing endpoints.

    Pipeline code continues to use Agent model. Public fields that are not
    available in a particular view are included as null.
    """

    agent_id: UUID
    miner_hotkey: str
    name: str
    version_num: int
    status: AgentStatus
    created_at: datetime
    emission: float | None = None
    reward_weight: float | None = None
    competition_state: AgentCompetitionState | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_public_agent(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data

        values = dict(data)
        if "agent_id" not in values and "id" in values:
            values["agent_id"] = values.pop("id")

        set_id = values.get("set_id")
        if set_id is not None:
            values["competition_state"] = build_agent_competition_state(
                status=values["status"],
                set_id=set_id,
                approved=values.get("approved") is True,
                approval_review_status=values.get("approval_review_status"),
                rank=values.get("rank"),
                final_score=values.get("final_score"),
                validator_count=values.get("validator_count"),
                validator_hotkeys=values.get("validator_hotkeys"),
                average_cost_usd=values.get("average_cost_usd"),
                average_runtime_seconds=values.get("average_runtime_seconds"),
                disqualified=values.get("disqualified"),
                approved_at=values.get("approved_at"),
                performance_delta=values.get("performance_delta"),
                cost_delta=values.get("cost_delta"),
                relative_improvement_units=values.get("relative_improvement_units"),
                time_multiplier=values.get("time_multiplier"),
                initial_reward_score=values.get("initial_reward_score"),
                baseline_agent_id=values.get("baseline_agent_id"),
                baseline_agent_name=values.get("baseline_agent_name"),
                baseline_agent_version_num=values.get("baseline_agent_version_num"),
            )
        return values

    # Temporary aliases for the current FE
    @computed_field
    @property
    def id(self) -> UUID:
        return self.agent_id

    @computed_field
    @property
    def set_id(self) -> int | None:
        return None if self.competition_state is None else self.competition_state.set_id

    @computed_field
    @property
    def approved(self) -> bool | None:
        return None if self.competition_state is None else self.competition_state.approved

    @computed_field
    @property
    def approval_review_status(self) -> ApprovalReviewStatus | None:
        return None if self.competition_state is None else self.competition_state.approval_review_status

    @computed_field
    @property
    def rank(self) -> int | None:
        return None if self.competition_state is None else self.competition_state.rank

    @computed_field
    @property
    def final_score(self) -> float | None:
        return None if self.competition_state is None else self.competition_state.final_score

    @computed_field
    @property
    def validator_count(self) -> int | None:
        return None if self.competition_state is None else self.competition_state.validator_count

    @computed_field
    @property
    def validator_hotkeys(self) -> list[str] | None:
        return None if self.competition_state is None else self.competition_state.validator_hotkeys

    @computed_field
    @property
    def average_cost_usd(self) -> float | None:
        return None if self.competition_state is None else self.competition_state.average_cost_usd

    @computed_field
    @property
    def average_runtime_seconds(self) -> float | None:
        return None if self.competition_state is None else self.competition_state.average_runtime_seconds

    @computed_field
    @property
    def disqualified(self) -> bool | None:
        return None if self.competition_state is None else self.competition_state.disqualified

    @computed_field
    @property
    def approved_at(self) -> datetime | None:
        return None if self.competition_state is None else self.competition_state.approved_at

    @computed_field
    @property
    def performance_delta(self) -> float | None:
        return None if self.competition_state is None else self.competition_state.performance_delta

    @computed_field
    @property
    def cost_delta(self) -> float | None:
        return None if self.competition_state is None else self.competition_state.cost_delta

    @computed_field
    @property
    def relative_improvement_units(self) -> float | None:
        return None if self.competition_state is None else self.competition_state.relative_improvement_units

    @computed_field
    @property
    def time_multiplier(self) -> float | None:
        return None if self.competition_state is None else self.competition_state.time_multiplier

    @computed_field
    @property
    def initial_reward_score(self) -> float | None:
        return None if self.competition_state is None else self.competition_state.initial_reward_score

    @computed_field
    @property
    def baseline_agent_id(self) -> UUID | None:
        return None if self.competition_state is None else self.competition_state.baseline_agent_id

    @computed_field
    @property
    def baseline_agent_name(self) -> str | None:
        return None if self.competition_state is None else self.competition_state.baseline_agent_name

    @computed_field
    @property
    def baseline_agent_version_num(self) -> int | None:
        return None if self.competition_state is None else self.competition_state.baseline_agent_version_num


class AgentCreate(AgentBase):
    """Upload input; transactional admission derives the agent status."""

    model_config = ConfigDict(extra="forbid")

    # Hash of the block containing the payment extrinsic associated with this
    # agent upload
    payment_block_hash: str
    # Index of the payment extrinsic within the block
    payment_extrinsic_index: str
