from enum import Enum
from typing import Any

from pydantic import BaseModel


class PreScreeningVerdict(str, Enum):
    pass_ = "pass"
    fail = "fail"
    needs_review = "needs_review"


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
    raw_response: dict[str, Any]
    error_message: str | None = None
