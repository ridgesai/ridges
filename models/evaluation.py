from datetime import datetime
from typing import Optional
from enum import Enum
from uuid import UUID
from pydantic import BaseModel

class EvaluationStatus(str, Enum):
    success = 'success'
    running = 'running'
    failure = 'failure'

class Evaluation(BaseModel):
    evaluation_id: UUID
    agent_id: UUID
    validator_hotkey: str
    set_id: int
    created_at: datetime
    finished_at: Optional[datetime] = None

class EvaluationWithStatus(Evaluation):
    status: EvaluationStatus