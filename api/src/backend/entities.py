## Defines the structures that we expect to get back from the database manager. Does not map 1-1 with the actual tables
from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum

class MinerAgent(BaseModel): 
    """Maps to the agent_versions table"""
    version_id: UUID
    miner_hotkey: str
    agent_name: str
    version_num: int
    created_at: datetime
    score: Optional[float]
    status: str

class AgentWithHydratedCode(MinerAgent):
    code: str

class EvaluationStatus(Enum):
    waiting = "waiting"
    running = "running" 
    completed = "completed"
    replaced = "replaced"
    timedout = "timedout"
    error = "error"

class Evaluation(BaseModel):
    evaluation_id: UUID
    version_id: UUID
    validator_hotkey: str
    status: EvaluationStatus
    terminated_reason: Optional[str]
    created_at: datetime
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    score: Optional[float]

class SandboxStatus(Enum):
    started = "started"
    sandbox_created = "sandbox_created"
    patch_generated = "patch_generated"
    eval_started = "eval_started"
    result_scored = "result_scored"
    cancelled = "cancelled"

class EvaluationRun(BaseModel):
    run_id: UUID
    evaluation_id: UUID
    swebench_instance_id: str
    response: Optional[str]
    error: Optional[str]
    pass_to_fail_success: Optional[str]
    fail_to_pass_success: Optional[str]
    pass_to_pass_success: Optional[str]
    fail_to_fail_success: Optional[str]
    solved: Optional[bool]
    status: SandboxStatus
    started_at: datetime
    sandbox_created_at: Optional[datetime]
    patch_generated_at: Optional[datetime]
    eval_started_at: Optional[datetime]
    result_scored_at: Optional[datetime]
    cancelled_at: Optional[datetime]
    
class EvaluationsWithHydratedRuns(Evaluation):
    evaluation_runs: list[EvaluationRun]

class ValidatorInfo(BaseModel):
    """Information about a connected validator"""
    validator_hotkey: Optional[str] = None
    version_commit_hash: Optional[str] = None
    connected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ip_address: Optional[str] = None
    is_screener: Optional[bool] = False
    status: Optional[str] = None