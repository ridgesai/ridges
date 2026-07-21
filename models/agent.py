from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


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


class AgentBase(BaseModel):
    miner_hotkey: str

    name: str
    version_num: int

    status: AgentStatus

    created_at: datetime
    ip_address: Optional[str] = None


class Agent(AgentBase):
    agent_id: UUID
    approval_review_status: ApprovalReviewStatus | None = None


class AgentCreate(AgentBase):
    """Schema used to create a new agent."""

    # Hash of the block containing the payment extrinsic associated with this
    # agent upload
    payment_block_hash: str
    # Index of the payment extrinsic within the block
    payment_extrinsic_index: str


class PossiblyBenchmarkAgent(Agent):
    is_benchmark_agent: bool
    benchmark_description: Optional[str] = None
    approved: bool = False
    performance_delta: Optional[float] = None
    cost_delta: Optional[float] = None
    relative_improvement_units: Optional[float] = None
    time_multiplier: Optional[float] = None
    initial_reward_score: Optional[float] = None
    approved_at: Optional[datetime] = None
    baseline_agent_id: Optional[UUID] = None
    baseline_agent_name: Optional[str] = None
    baseline_agent_version_num: Optional[int] = None


# TODO ADAM: need to look into this more


class BenchmarkAgentScored(Agent):
    benchmark_description: Optional[str] = None

    set_id: int
    approved: bool
    validator_count: int
    final_score: float


class AgentScored(Agent):
    set_id: int
    approved: bool
    validator_count: int
    final_score: float
