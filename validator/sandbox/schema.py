from datetime import datetime
from typing import Optional
from enum import Enum

from pydantic import BaseModel, Field

from loggers.logging_utils import get_logger

logger = get_logger(__name__)

class SandboxState(Enum):
    """Sandbox execution states in lifecycle order"""
    CREATED = "created"
    PATCH_GENERATING = "patch_generating"
    PATCH_GENERATED = "patch_generated"
    EVALUATING = "evaluating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class SwebenchProblem(BaseModel):
    instance_id: str
    problem_statement: str
    repo: str
    base_commit: str
    test_patch: str
    
    def to_dict(self):
        return {
            "instance_id": self.instance_id,
            "problem_statement": self.problem_statement,
            "repo": self.repo,
            "base_commit": self.base_commit,
            "test_patch": self.test_patch,
        }

class SandboxInput(SwebenchProblem):
    run_id: str

class AgentVersion(BaseModel):
    version_id: str
    miner_hotkey: str
    version_num: int
    created_at: datetime = Field(default_factory=datetime.now)
    score: Optional[float] = None
    
class EvaluationRun(BaseModel):
    run_id: str
    evaluation_id: str
    swebench_instance_id: str
    response: Optional[str] = None
    error: Optional[str] = None
    fail_to_pass_success: Optional[str] = None
    pass_to_pass_success: Optional[str] = None
    fail_to_fail_success: Optional[str] = None
    pass_to_fail_success: Optional[str] = None
    solved: Optional[bool] = None
    status: str
    started_at: datetime
    sandbox_created_at: Optional[datetime] = None
    patch_generated_at: Optional[datetime] = None
    eval_started_at: Optional[datetime] = None
    result_scored_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None

    def to_dict(self):
        return {
            "run_id": self.run_id,
            "evaluation_id": self.evaluation_id,
            "swebench_instance_id": self.swebench_instance_id,
            "response": self.response,
            "error": self.error,
            "fail_to_pass_success": self.fail_to_pass_success,
            "pass_to_pass_success": self.pass_to_pass_success,
            "fail_to_fail_success": self.fail_to_fail_success,
            "pass_to_fail_success": self.pass_to_fail_success,
            "solved": self.solved,
            "status": self.status,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "sandbox_created_at": self.sandbox_created_at.isoformat() if self.sandbox_created_at else None,
            "patch_generated_at": self.patch_generated_at.isoformat() if self.patch_generated_at else None,
            "eval_started_at": self.eval_started_at.isoformat() if self.eval_started_at else None,
            "result_scored_at": self.result_scored_at.isoformat() if self.result_scored_at else None,
            "cancelled_at": self.cancelled_at.isoformat() if self.cancelled_at else None,
        }