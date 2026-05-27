from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ApprovalJobStatus(str, Enum):
    pending = "pending"
    running = "running"
    error = "error"
    completed = "completed"
    needs_review = "needs_review"


class ApprovalProcessingStatus(str, Enum):
    pending = "pending"
    running = "running"
    error = "error"
    completed = "completed"
    needs_review = "needs_review"


class ApprovalVerdict(str, Enum):
    approved = "approved"
    rejected = "rejected"
    needs_review = "needs_review"


class ApprovalSourceReference(BaseModel):
    type: Literal["s3_key"]
    key: str
    file: str


class ApprovalValidatorScore(BaseModel):
    validator_hotkey: str
    score: float


class ApprovalPreScreeningContext(BaseModel):
    verdict: str | None = None
    confidence: float | None = None
    summary: str | None = None
    policy_version: str | None = None
    resolution: Literal["auto", "human"] | None = None


class ApprovalEvaluationContext(BaseModel):
    final_validator_score: float | None = None
    validator_count: int = Field(ge=0)
    validator_scores: list[ApprovalValidatorScore] = Field(default_factory=list)
    pre_screening: ApprovalPreScreeningContext | None = None


class ApprovalInputSnapshot(BaseModel):
    agent_id: UUID
    set_id: int
    source: ApprovalSourceReference
    evaluation_context: ApprovalEvaluationContext


class ApprovalJob(BaseModel):
    job_id: UUID
    agent_id: UUID
    set_id: int
    status: ApprovalJobStatus
    attempt_count: int
    claim_token: UUID | None = None
    claimed_at: datetime | None = None
    claimed_by: str | None = None
    lease_expires_at: datetime | None = None
    next_attempt_at: datetime
    last_error: str | None = None
    policy_version: str
    input_snapshot: dict[str, Any]
    aggregate_verdict: ApprovalVerdict | None = None
    aggregate_score: float | None = None
    aggregate_confidence: float | None = None
    aggregate_summary: str | None = None
    projected_at: datetime | None = None
    decision_source: str | None = None
    discord_channel_id: str | None = None
    discord_message_id: str | None = None
    review_requested_at: datetime | None = None
    reviewer_id: str | None = None
    reviewed_at: datetime | None = None
    review_decision_reason: str | None = None
    announcement_sent_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ApprovalJobRound(BaseModel):
    job_id: UUID
    round_index: int
    model: str
    verdict: ApprovalVerdict
    approval_score: float
    confidence: float
    summary: str
    evidence: list[dict[str, Any]]
    raw_response: dict[str, Any]
    created_at: datetime


class AgentApprovalState(BaseModel):
    agent_id: UUID
    set_id: int
    latest_job_id: UUID | None = None
    processing_status: ApprovalProcessingStatus
    system_verdict: ApprovalVerdict | None = None
    system_score: float | None = None
    system_confidence: float | None = None
    system_summary: str | None = None
    published_verdict: ApprovalVerdict | None = None
    published_score: float | None = None
    updated_at: datetime
