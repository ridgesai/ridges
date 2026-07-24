from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class DisqualifiedAgent(BaseModel):
    agent_id: UUID
    reason: str
    disqualified_at: datetime
