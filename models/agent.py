from enum import Enum
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel



class AgentStatus(str, Enum):
    screening_1 = 'screening_1'
    failed_screening_1 = 'failed_screening_1'
    screening_2 = 'screening_2'
    failed_screening_2 = 'failed_screening_2'
    evaluating = 'evaluating'
    finished = 'finished'
    cancelled = 'cancelled'



class Agent(BaseModel):
    agent_id: UUID
    miner_hotkey: str

    name: str
    version_num: int

    status: AgentStatus

    created_at: datetime