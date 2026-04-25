from datetime import datetime
from typing import Any, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from models.agent import Agent
from models.evaluation import Evaluation
from models.evaluation_run import EvaluationRunStatus
from models.problem import ProblemTestResult
from utils.system_metrics import SystemMetrics


class ValidatorRegistrationRequest(BaseModel):
    timestamp: int
    signed_timestamp: str
    hotkey: str
    commit_hash: str


class ValidatorRegistrationResponse(BaseModel):
    session_id: UUID
    running_agent_timeout_seconds: int
    running_eval_timeout_seconds: int
    max_evaluation_run_log_size_bytes: int


class ScreenerRegistrationRequest(BaseModel):
    name: str
    password: str
    commit_hash: str


class ScreenerRegistrationResponse(BaseModel):
    session_id: UUID
    running_agent_timeout_seconds: int
    running_eval_timeout_seconds: int
    max_evaluation_run_log_size_bytes: int


class ValidatorRequestEvaluationRequest(BaseModel):
    pass


class ValidatorRequestEvaluationResponseEvaluationRun(BaseModel):  # :(
    evaluation_run_id: UUID
    problem_name: str
    problem_suite_name: str | None = None
    benchmark_family: str | None = None
    execution_spec: dict[str, Any] | None = None


class ValidatorRequestEvaluationResponse(BaseModel):
    agent_code: str
    evaluation_runs: List[ValidatorRequestEvaluationResponseEvaluationRun]
    artifact_upload_urls: dict[str, str] = Field(default_factory=dict)


class ValidatorTaskDownloadUrlRequest(BaseModel):
    task_digest: str


class ValidatorTaskDownloadUrlResponse(BaseModel):
    url: str


class ValidatorHeartbeatRequest(BaseModel):
    system_metrics: SystemMetrics


class ValidatorHeartbeatResponse(BaseModel):
    pass


class ValidatorUpdateEvaluationRunRequest(BaseModel):
    evaluation_run_id: UUID
    updated_status: EvaluationRunStatus

    patch: Optional[str] = None
    test_results: Optional[List[ProblemTestResult]] = None
    verifier_reward: Optional[float] = None

    agent_logs: Optional[str] = None
    eval_logs: Optional[str] = None

    error_code: Optional[int] = None
    error_message: Optional[str] = None


class ValidatorUpdateEvaluationRunResponse(BaseModel):
    pass


class ValidatorDisconnectRequest(BaseModel):
    reason: str


class ValidatorDisconnectResponse(BaseModel):
    pass


class ValidatorFinishEvaluationRequest(BaseModel):
    pass


class ValidatorFinishEvaluationResponse(BaseModel):
    pass


class ConnectedValidatorInfo(BaseModel):
    name: str
    hotkey: str
    time_connected: datetime

    time_last_heartbeat: Optional[datetime] = None
    system_metrics: Optional[SystemMetrics] = None

    evaluation: Optional[Evaluation] = None
    agent: Optional[Agent] = None
