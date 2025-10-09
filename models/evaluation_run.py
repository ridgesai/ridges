from enum import Enum
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel
from typing import List, Optional
from models.problem import ProblemTestResult


class EvaluationRunStatus(str, Enum):
    pending = 'pending'
    initializing_agent = 'initializing_agent'
    running_agent = 'running_agent'
    initializing_eval = 'initializing_eval'
    running_eval = 'running_eval'
    finished = 'finished'
    error = 'error'



class EvaluationRun(BaseModel):
    evaluation_run_id: UUID
    evaluation_id: UUID
    problem_name: str

    status: EvaluationRunStatus

    patch: Optional[str] = None
    test_results: Optional[List[ProblemTestResult]] = None

    error_code: Optional[int] = None
    error_message: Optional[str] = None

    created_at: datetime
    started_initializing_agent_at: Optional[datetime] = None
    started_running_agent_at: Optional[datetime] = None
    started_initializing_eval_at: Optional[datetime] = None
    started_running_eval_at: Optional[datetime] = None
    finished_or_errored_at: Optional[datetime] = None