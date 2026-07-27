from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class DisqualificationJob(BaseModel):
    id: UUID
    agent_id: UUID
    set_id: int
    created_at: datetime
    processed_at: datetime | None = None
    attempts: int = 0
    error: str | None = None
