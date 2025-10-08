from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel



class Evaluation(BaseModel):
    evaluation_id: UUID
    agent_id: UUID
    validator_hotkey: str
    set_id: int
    created_at: datetime
    finished_at: Optional[datetime] = None
