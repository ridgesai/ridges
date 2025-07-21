## Defines the structures that we expect to get back from the database manager. Does not map 1-1 with the actual tables
from datetime import datetime, timezone
from uuid import UUID

from fastapi import WebSocket
from pydantic import BaseModel, Field
from typing import Literal, Optional, TYPE_CHECKING
from enum import Enum


class MinerAgent(BaseModel): 
    """Maps to the agent_versions table"""
    version_id: UUID
    miner_hotkey: str
    agent_name: str
    version_num: int
    created_at: datetime
    status: str
    agent_summary: Optional[str] = None

class AgentWithHydratedCode(MinerAgent):
    code: str

class MinerAgentWithScores(MinerAgent):
    """MinerAgent with computed scores by set_id"""
    score: Optional[float]
    set_id: Optional[int]
    
class AgentStatus(Enum):
    """States for miner agents - clear and unambiguous"""
    awaiting_screening = "awaiting_screening"          # Just uploaded, needs screening
    screening = "screening"                  # Currently being screened  
    failed_screening = "failed_screening"              # Failed screening (score < 0.8)
    waiting = "waiting"           # Passed screening, needs evaluation
    evaluating = "evaluating"               # Currently being evaluated
    scored = "scored"                       # All evaluations complete
    replaced = "replaced"                   # Replaced by newer version
    
    @classmethod
    def from_string(cls, status: str) -> 'AgentStatus':
        """Map database status string to agent state enum"""
        mapping = {
            "awaiting_screening": cls.awaiting_screening,
            "screening": cls.screening,
            "failed_screening": cls.failed_screening,
            "waiting": cls.waiting,
            "evaluating": cls.evaluating,
            "scored": cls.scored,
            "replaced": cls.replaced
        }
        return mapping.get(status, cls.awaiting_screening)

class EvaluationStatus(Enum):
    awaiting_screening = "awaiting_screening"
    waiting = "waiting"
    running = "running" 
    completed = "completed"
    replaced = "replaced"
    error = "error"
    
    @classmethod
    def from_string(cls, status: str) -> 'EvaluationStatus':
        """Map database status string to evaluation state enum"""
        mapping = {
            "awaiting_screening": cls.awaiting_screening,
            "waiting": cls.waiting,
            "running": cls.running,
            "completed": cls.completed,
            "error": cls.error,
            "replaced": cls.replaced
        }
        return mapping.get(status, cls.waiting)

class Evaluation(BaseModel):
    evaluation_id: UUID
    version_id: UUID
    validator_hotkey: str
    set_id: int
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
    

class EvaluationRunWithUsageDetails(EvaluationRun):
    cost: Optional[float]
    total_tokens: Optional[int]
    model: Optional[str]
    num_inference_calls: Optional[int]

class EvaluationsWithHydratedRuns(Evaluation):
    evaluation_runs: list[EvaluationRun]

class EvaluationsWithHydratedUsageRuns(Evaluation):
    evaluation_runs: list[EvaluationRunWithUsageDetails]

class Client(BaseModel):
    """Base class for connected clients"""
    model_config = {"arbitrary_types_allowed": True}

    client_id: Optional[str] = None
    connected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ip_address: Optional[str] = None
    websocket: Optional[WebSocket] = None

    def get_type(self) -> str:
        """Return the type of client"""
        return "client"

class Inference(BaseModel):
    id: UUID
    run_id: UUID
    message: str
    temperature: float
    model: str
    cost: float
    response: str
    total_tokens: int
    created_at: datetime
    finished_at: Optional[datetime]

class EvaluationQueueItem(BaseModel):
    evaluation_id: UUID
    version_id: UUID
    created_at: datetime

class ValidatorQueueInfo(BaseModel):
    validator_hotkey: str
    queue_size: int
    queue: list[EvaluationQueueItem]