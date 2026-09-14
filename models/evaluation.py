from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel

from models.evaluation_run import PublicEvaluationRun
from models.evaluation_set import EvaluationSetGroup


class EvaluationStatus(str, Enum):
    success = "success"
    running = "running"
    failure = "failure"


class Evaluation(BaseModel):
    evaluation_id: UUID
    agent_id: UUID
    validator_hotkey: str
    set_id: int
    evaluation_set_group: EvaluationSetGroup
    created_at: datetime
    finished_at: Optional[datetime] = None


class PublicEvaluationWithRuns(Evaluation):
    runs: list[PublicEvaluationRun]


class HydratedEvaluation(Evaluation):
    status: EvaluationStatus
    score: float
