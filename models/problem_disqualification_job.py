from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from models.evaluation_set import EvaluationSetGroup


class ProblemDisqualificationJob(BaseModel):
    id: UUID
    set_id: int
    set_group: EvaluationSetGroup
    problem_name: str
    created_at: datetime
    processed_at: datetime | None = None
    attempts: int = 0
    error: str | None = None
