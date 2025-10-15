from uuid import UUID
from pydantic import BaseModel
class TopAgentHotkey(BaseModel):
    miner_hotkey: str
    agent_id: UUID
    avg_score: float

