## Defines the structures that we expect to get back from the database manager. Does not map 1-1 with the actual tables
from datetime import datetime

from pydantic import BaseModel
from typing import Optional
from enum import Enum

class MinerAgent(BaseModel): 
    """Maps to the agent_versions table"""
    version_id: str
    miner_hotkey: str
    agent_name: str
    version_num: int
    created_at: datetime
    score: float

class AgentWithHydratedCode(MinerAgent):
    code: str

class EvaluationStatus(Enum):
    waiting = "waiting"
    running = "running" 
    completed = "completed"
    replaced = "replaced"

class Evaluation(BaseModel):
    evaluation_id: str
    version_id: str
    validator_hotkey: str
    status: EvaluationStatus
    terminated_reason: Optional[str]
    created_at: datetime
    started_at: datetime
    finished_at: datetime
    score: Optional[float]

class SandboxStatus(Enum):
    started = "started"
    sandbox_created = "sandbox_created"
    patch_generate = "patch_generate"
    eval_started = "eval_started"
    result_scored = "result_scored"

class EvaluationRun(BaseModel):
    run_id: str
    evaluation_id: str
    swebench_instance_id: str
    response: Optional[str]
    error: Optional[str]
    pass_to_fail_success: str
    fail_to_pass_success: str
    pass_to_pass_success: str
    fail_to_fail_success: str
    solved: bool
    status: SandboxStatus
    started_at: datetime
    sandbox_created_at: datetime
    patch_generated_at: datetime
    eval_started_at: datetime
    result_scored_at: datetime
    
