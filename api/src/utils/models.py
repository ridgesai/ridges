from pydantic import BaseModel
from datetime import datetime
from typing import Optional, Literal

class Agent(BaseModel):
    agent_id: str
    miner_hotkey: str
    latest_version: int
    created_at: datetime
    last_updated: datetime

class AgentVersion(BaseModel):
    version_id: str
    agent_id: str
    version_num: int
    created_at: datetime
    score: Optional[float]

class AgentVersionForValidator(AgentVersion):
    miner_hotkey: str

class EvaluationRun(BaseModel):
    run_id: str
    evaluation_id: str
    swebench_instance_id: str
    response: Optional[str]
    error: Optional[str]
    pass_to_fail_success: Optional[str]
    fail_to_pass_success: Optional[str]
    pass_to_pass_success: Optional[str]
    fail_to_fail_success: Optional[str]
    solved: Optional[bool]
    started_at: datetime
    finished_at: Optional[datetime]

class Evaluation(BaseModel):
    evaluation_id: str
    version_id: str
    validator_hotkey: str
    status: Literal["waiting", "running", "completed", "timedout", "disconnected", "error"]
    terminated_reason: Optional[str]
    created_at: datetime
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
