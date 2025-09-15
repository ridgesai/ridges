import datetime
from pydantic import BaseModel, Field
from typing import Any, Literal
from enum import Enum
from datetime import datetime

class ValidatorMessageType(Enum):
    HEARTBEAT = "heartbeat"

class BaseMessage(BaseModel):
    type: str
    timestamp: datetime = Field(default_factory=datetime.now)
    validator_hotkey: str
    version_commit_hash: str

class Authentication(BaseMessage):
    type: Literal["validator-info"] = "validator-info"
    # timestamp + hotkey + version sig
    signature: str

class Heartbeat(BaseMessage):
    type: Literal["heartbeat"] = "heartbeat"

class RequestNextEvaluation(BaseMessage):
    type: Literal["get-next-evaluation"] = "get-next-evaluation"
    # timestamp + hotkey + version sig
    signature: str

class StartEvaluation(BaseMessage):
    type: Literal["start-evaluation"] = "start-evaluation"
    #  hotkey + version sig
    evaluation_id: str
    agent_info: str

class EvaluationRunStatus(Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

class UpsertEvaluationRun(BaseMessage):
    type: Literal["upsert-evaluation-run"] = "upsert-evaluation-run"
    evaluation_id: str
    run_id: str
    status: EvaluationRunStatus

class FinishEvaluation(BaseMessage):
    type: Literal["finish-evaluation"] = "finish-evaluation"
    evaluation_id: str
    final_score: float 

## Messages sent by platform 

def construct_message_for_socket(
    message_type: str,
    payload: dict[str, Any]
):
    match message_type:
        case "authenticaton":
            return {
                "event": "validator-info",
            }

        case "heartbeat":
            pass

        case "evaluation_result":
            pass


def parse_message_from_socket():
    pass