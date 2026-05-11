from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class PreScreeningJobStatus(str, Enum):
    pending = "pending"
    running = "running"
    error = "error"
    succeeded = "succeeded"
    failed = "failed"
    needs_review = "needs_review"


class PreScreeningVerdict(str, Enum):
    pass_ = "pass"
    fail = "fail"
    needs_review = "needs_review"


class PreScreeningJob(BaseModel):
    job_id: UUID
    agent_id: UUID
    status: PreScreeningJobStatus
    attempt_count: int
    claim_token: UUID | None = None
    claimed_at: datetime | None = None
    lease_expires_at: datetime | None = None
    claimed_by: str | None = None
    next_attempt_at: datetime
    last_error: str | None = None
    policy_version: str
    created_at: datetime
    updated_at: datetime


class PreScreeningJudgeSource(BaseModel):
    type: Literal["presigned_url"]
    url: str
    file: str


class PreScreeningJudgeRequest(BaseModel):
    job_id: UUID
    agent_id: UUID
    source: PreScreeningJudgeSource
    policy_version: str


class PreScreeningEvidence(BaseModel):
    file: str
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    reason: str = Field(max_length=200)


class PreScreeningJudgeResponse(BaseModel):
    verdict: PreScreeningVerdict
    confidence: float = Field(ge=0, le=1)
    summary: str = Field(max_length=1000)
    categories: list[str] = Field(default_factory=list)
    evidence: list[PreScreeningEvidence] = Field(default_factory=list)
    static_findings: list[str] = Field(default_factory=list)
    model: str | None = None
    fallback_used: bool = False
    policy_version: str
    source_sha256: str | None = None


class PreScreeningModelResponse(BaseModel):
    verdict: PreScreeningVerdict
    confidence: float = Field(ge=0, le=1)
    summary: str = Field(max_length=1000)
    categories: list[str] = Field(default_factory=list)
    evidence: list[PreScreeningEvidence] = Field(default_factory=list)


class PreScreeningResultPayload(BaseModel):
    verdict: PreScreeningVerdict
    confidence: float
    summary: str
    categories: list[str]
    evidence: list[dict[str, Any]]
    static_findings: list[str]
    model: str | None
    fallback_used: bool
    policy_version: str
    source_sha256: str | None
    raw_response: dict[str, Any]
    error_message: str | None = None
