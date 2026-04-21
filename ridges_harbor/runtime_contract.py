"""Host-side contract types for the Ridges Harbor adapter/runtime."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RidgesAgentPhaseError(RuntimeError):
    """Base class for agent-phase failures that must escape Harbor's inner catch."""


class MinerRuntimeError(RidgesAgentPhaseError):
    """Raised so Harbor skips verifier when the runtime exits non-zero."""


class MinerInvalidPatchError(RidgesAgentPhaseError):
    """Raised so Harbor skips verifier when patch validation fails."""


class MinerPatchApplyError(RidgesAgentPhaseError):
    """Raised so Harbor skips verifier when applying the patch fails."""


class RidgesRuntimeExceptionChainItem(BaseModel):
    """One exception in the captured runtime exception chain."""

    model_config = ConfigDict(extra="ignore")

    type: str
    module: str
    message: str


class RidgesRuntimeFailure(BaseModel):
    """Structured failure details emitted by the Harbor-side runtime wrapper."""

    model_config = ConfigDict(extra="ignore")

    phase: str
    traceback: str
    http_status: int | None = None
    missing_module: str | None = None
    exception_chain: list[RidgesRuntimeExceptionChainItem] = Field(default_factory=list)
