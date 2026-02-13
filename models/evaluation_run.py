from uuid import UUID
from datetime import datetime
from enum import Enum, IntEnum
from pydantic import BaseModel
from typing import List, Optional
from models.problem import ProblemTestResult



class EvaluationRunErrorCode(IntEnum):
    # ADAM: Magic
    def __new__(cls, code: int, message: str):
        obj = int.__new__(cls, code)
        obj._value_ = code
        obj.message = message
        return obj
    
    # 1xxx - Agent Errors
    AGENT_EXCEPTION_RUNNING_AGENT = (1000, "The agent raised an exception while being run")
    AGENT_EXCEPTION_RUNNING_EVAL  = (1010, "The agent raised an exception while being evaluated")
    AGENT_TIMEOUT_RUNNING_AGENT   = (1020, "The agent timed out while being run")
    AGENT_TIMEOUT_RUNNING_EVAL    = (1030, "The agent timed out while being evaluated")
    AGENT_INVALID_PATCH           = (1040, "The agent returned an invalid patch")

    # 2xxx - Validator Errors
    VALIDATOR_INTERNAL_ERROR       = (2000, "An internal error occurred on the validator")
    VALIDATOR_FAILED_PENDING       = (2010, "An internal error occurred on the validator while the evaluation run was pending")
    VALIDATOR_FAILED_INIT_AGENT    = (2020, "An internal error occurred on the validator while the evaluation run was initializing the agent")
    VALIDATOR_FAILED_RUNNING_AGENT = (2030, "An internal error occurred on the validator while the evaluation run was running the agent")
    VALIDATOR_FAILED_INIT_EVAL     = (2040, "An internal error occurred on the validator while the evaluation run was initializing the evaluation")
    VALIDATOR_FAILED_RUNNING_EVAL  = (2050, "An internal error occurred on the validator while the evaluation run was running the evaluation")
    VALIDATOR_UNKNOWN_PROBLEM      = (2060, "Unknown problem")

    # 3xxx - Platform Errors
    PLATFORM_RESTARTED_WHILE_PENDING       = (3000, "The platform was restarted while the evaluation run was pending")
    PLATFORM_RESTARTED_WHILE_INIT_AGENT    = (3010, "The platform was restarted while the evaluation run was initializing the agent")
    PLATFORM_RESTARTED_WHILE_RUNNING_AGENT = (3020, "The platform was restarted while the evaluation run was running the agent")
    PLATFORM_RESTARTED_WHILE_INIT_EVAL     = (3030, "The platform was restarted while the evaluation run was initializing the evaluation")
    PLATFORM_RESTARTED_WHILE_RUNNING_EVAL  = (3040, "The platform was restarted while the evaluation run was running the evaluation")

    def get_error_message(self) -> str:
        return self.message
    
    def is_agent_error(self) -> bool:
        return 1000 <= self.value < 2000
    
    def is_validator_error(self) -> bool:
        return 2000 <= self.value < 3000

    def is_platform_error(self) -> bool:
        return 3000 <= self.value < 4000



class EvaluationRunStatus(str, Enum):
    pending = 'pending'
    initializing_agent = 'initializing_agent'
    running_agent = 'running_agent'
    initializing_eval = 'initializing_eval'
    running_eval = 'running_eval'
    finished = 'finished'
    error = 'error'
    skipped = 'skipped'



class EvaluationRun(BaseModel):
    evaluation_run_id: UUID
    evaluation_id: UUID
    problem_name: str

    status: EvaluationRunStatus

    patch: Optional[str] = None
    test_results: Optional[List[ProblemTestResult]] = None

    error_code: Optional[EvaluationRunErrorCode] = None
    error_message: Optional[str] = None

    created_at: datetime
    started_initializing_agent_at: Optional[datetime] = None
    started_running_agent_at: Optional[datetime] = None
    started_initializing_eval_at: Optional[datetime] = None
    started_running_eval_at: Optional[datetime] = None
    finished_or_errored_at: Optional[datetime] = None



class EvaluationRunLogType(str, Enum):
    agent = 'agent'
    eval = 'eval'