from typing import Any, Optional
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel
from api.src.backend.entities import MinerAgent, Inference

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

class QueuePositionPerValidator(BaseModel): 
    validator_hotkey: str
    queue_position: int

async def get_queue_position_by_hotkey(miner_hotkey: str) -> list[QueuePositionPerValidator]: ...
async def get_inference_details_for_run(run_id: str) -> list[Inference]: ...

async def get_agent_scores_over_time(set_id: Optional[int] = None) -> list[dict]: ...

async def get_miner_score_activity(set_id: Optional[int] = None) -> list[dict]: ...