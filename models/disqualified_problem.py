from datetime import datetime

from pydantic import BaseModel

from models.evaluation_set import EvaluationSetGroup


class DisqualifiedProblem(BaseModel):
    set_id: int
    set_group: EvaluationSetGroup
    problem_name: str
    reason: str
    created_at: datetime
