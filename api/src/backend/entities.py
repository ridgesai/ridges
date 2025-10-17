## Defines the structures that we expect to get back from the database manager. Does not map 1-1 with the actual tables
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import UUID

from models.agent import AgentStatus
from pydantic import BaseModel, EmailStr


# TODO: ADAM - Used in agents.py -> for open_users stuff + below + upload_agent_helpers.py
class MinerAgent(BaseModel):
    """Maps to the agent_versions table"""
    model_config = { "arbitrary_types_allowed": True }
    
    agent_id: UUID
    miner_hotkey: str
    name: str
    version_num: int
    created_at: datetime
    status: AgentStatus
    agent_summary: Optional[str] = None
    ip_address: Optional[str] = None
    innovation_score: Optional[float] = None

# TODO: ADAM - Used below
class EvaluationStatus(Enum):
    waiting = "waiting"
    running = "running"
    replaced = "replaced"
    error = "error"
    completed = "completed"
    cancelled = "cancelled"
    pruned = "pruned"

    @classmethod
    def from_string(cls, status: str) -> 'EvaluationStatus':
        """Map database status string to evaluation state enum"""
        mapping = {
            "waiting": cls.waiting,
            "running": cls.running,
            "error": cls.error,
            "replaced": cls.replaced,
            "completed": cls.completed,
            "cancelled": cls.cancelled,
            "pruned": cls.pruned,
        }
        return mapping.get(status, cls.error)

# TODO: ADAM - Used in backend/queries/evaluations.py -> upload_agent_helpers.py
class Evaluation(BaseModel):
    model_config = { "arbitrary_types_allowed": True }
    
    evaluation_id: UUID
    agent_id: UUID
    validator_hotkey: str
    set_id: int
    status: EvaluationStatus
    terminated_reason: Optional[str]
    created_at: datetime
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    score: Optional[float]
    screener_score: Optional[float]

# TODO: ADAM - used in endpoints/open_users.py
class OpenUser(BaseModel):
    open_hotkey: str
    auth0_user_id: str
    email: str
    name: str
    registered_at: datetime
    agents: Optional[list[MinerAgent]] = []
    bittensor_hotkey: Optional[str] = None
    admin: Optional[int] = 7

# TODO: ADAM - used in endpoints/open_users.py
class OpenUserSignInRequest(BaseModel):
    auth0_user_id: str
    email: EmailStr
    name: str
    password: str