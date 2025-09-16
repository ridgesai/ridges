import datetime
from pydantic import BaseModel, Field
from typing import Any, Literal, Optional
from enum import Enum
from datetime import datetime

class BaseMessage(BaseModel):
    type: str
    timestamp: datetime = Field(default_factory=datetime.now)
    validator_hotkey: str = ""
    version_commit_hash: str = ""

class Authentication(BaseMessage):
    type: Literal["validator-info"] = "validator-info"
    # timestamp + hotkey + version sig
    signature: Optional[str]

class Heartbeat(BaseMessage):
    type: Literal["heartbeat"] = "heartbeat"
    status: str
    cpu_percent: Optional[float]
    ram_percent: Optional[float]
    ram_total_gb: Optional[float]
    disk_percent: Optional[float]
    disk_total_gb: Optional[float]
    containers: Optional[float]

class RequestNextEvaluation(BaseMessage):
    type: Literal["get-next-evaluation"] = "get-next-evaluation"

class StartEvaluation(BaseMessage):
    type: Literal["start-evaluation"] = "start-evaluation"
    evaluation_id: str
    agent_info: str
    signature: str

class EvaluationRunStatus(Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

class UpsertEvaluationRun(BaseMessage):
    type: Literal["upsert-evaluation-run"] = "upsert-evaluation-run"
    evaluation_id: str
    run_id: str
    status: EvaluationRunStatus
    signature: Optional[str]

class FinishEvaluation(BaseMessage):
    type: Literal["finish-evaluation"] = "finish-evaluation"
    evaluation_id: str
    final_score: float
    signature: Optional[str]

## Messages sent by platform 
ValidatorMessage = (
    Authentication
    | Heartbeat
    | RequestNextEvaluation
    | StartEvaluation
    | UpsertEvaluationRun
    | FinishEvaluation
)