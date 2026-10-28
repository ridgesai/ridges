from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from models.competition import AdminReason


class BenchmarkPhaseStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    reason: AdminReason


class BenchmarkPhaseEndRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    winner_agent_id: Annotated[UUID | None, Field(strict=False)] = None
    reason: AdminReason


class BenchmarkPoolEntry(BaseModel):
    agent_id: UUID
    name: str
    version_num: int
    miner_hotkey: str
    final_score: float | None
    avg_cost_usd: float | None
    entered_at: datetime
    eligible: bool
    ineligible_reason: str | None = None


class BenchmarkWinnerReward(BaseModel):
    score_points: float
    cost_points: float
    total_points: float


class BenchmarkPhaseSnapshot(BaseModel):
    phase_id: UUID
    set_id: int
    started_at: datetime
    started_by: str
    start_reason: str
    ended_at: datetime | None
    ended_by: str | None
    end_reason: str | None
    winner_agent_id: UUID | None
    default_winner_agent_id: UUID | None
    pool: list[BenchmarkPoolEntry]
    winner_reward: BenchmarkWinnerReward | None = None
