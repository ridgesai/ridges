from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel

from models.evaluation_run import EvaluationRun


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

# TODO ADAM: Should inherit from Evaluation and then add the runs member, not priority right now

class HydratedEvaluation(Evaluation):
    status: EvaluationStatus
    score: float


class EvaluationWithRuns(BaseModel):
    evaluation: Evaluation
    runs: list[EvaluationRun]
