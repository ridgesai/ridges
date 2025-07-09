from typing import Any
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel
from api.src.backend.entities import MinerAgent

async def get_24_hour_statistics() -> dict[str, Any]: ...

class RunningEvaluation(BaseModel):
    version_id: UUID
    validator_hotkey: str
    started_at: datetime
    agent_name: str
    miner_hotkey: str
    version_num: int

async def get_currently_running_evaluations() -> list[RunningEvaluation]: ...
async def get_top_agents(num_agents: int = 3) -> list[MinerAgent]: ...
async def get_agent_summary_by_hotkey(miner_hotkey: str) -> list[MinerAgent]: ...