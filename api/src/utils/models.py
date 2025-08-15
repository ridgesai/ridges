from uuid import UUID
from pydantic import BaseModel, Field
from datetime import datetime, timedelta
from typing import Optional, Literal, List

class AgentVersionResponse(BaseModel):
    version_id: str
    agent_id: str
    version_num: int
    created_at: datetime
    score: Optional[float]

class AgentResponse(BaseModel):
    agent_id: str
    miner_hotkey: str
    name: str
    latest_version: int
    created_at: datetime
    last_updated: datetime

class EvaluationRunResponse(BaseModel):
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
    status: Literal["started", "sandbox_created", "patch_generated", "eval_started", "result_scored"]
    started_at: datetime
    sandbox_created_at: Optional[datetime] = None
    patch_generated_at: Optional[datetime] = None
    eval_started_at: Optional[datetime] = None
    result_scored_at: Optional[datetime] = None

class EvaluationResponse(BaseModel):
    evaluation_id: str
    version_id: str
    validator_hotkey: str
    status: Literal["waiting", "running", "completed", "disconnected", "error"]
    terminated_reason: Optional[str]
    created_at: datetime
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    score: Optional[float]

class AgentSummary(BaseModel):
    miner_hotkey: str
    name: str
    latest_version: AgentVersionResponse
    code: Optional[str]

class TopAgentHotkey(BaseModel):
    miner_hotkey: str
    version_id: UUID
    avg_score: float

class AgentQueryResponse(BaseModel):
    agent_id: str
    latest_agent: AgentSummary
    latest_scored_agent: Optional[AgentSummary]

class Execution(BaseModel):
    evaluation: EvaluationResponse
    evaluation_runs: List[EvaluationRunResponse]
    agent: AgentResponse
    agent_version: AgentVersionResponse

### DO NOT REMOVE THESE MODELS ###

class EmbeddingRequest(BaseModel):
    input: str = Field(..., description="Text to embed")
    run_id: str = Field(..., description="Evaluation run ID")

class GPTMessage(BaseModel):
    role: str
    content: str
    
class InferenceRequest(BaseModel):
    run_id: str = Field(..., description="Evaluation run ID")
    model: Optional[str] = Field(None, description="Model to use for inference")
    temperature: Optional[float] = Field(None, description="Temperature for inference")
    messages: List[GPTMessage] = Field(..., description="Messages to send to the model")

class AgentVersionNew(BaseModel):
    version_id: str
    version_num: int
    created_at: datetime
    score: Optional[float]
    code: Optional[str]

class AgentDetailsNew(BaseModel):
    agent_id: str
    miner_hotkey: str
    name: str
    created_at: datetime

class AgentSummaryResponse(BaseModel):
    agent_details: AgentDetailsNew
    latest_version: AgentVersionNew
    all_versions: List[AgentVersionNew]
    daily_earnings: Optional[float] = None

class ExecutionNew(BaseModel):
    evaluation_id: str
    agent_version_id: str
    validator_hotkey: str
    status: Literal["waiting", "running", "completed", "error", "replaced"]
    terminated_reason: Optional[str]
    created_at: datetime
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    score: Optional[float]
    evaluation_runs: List[EvaluationRunResponse]

class AgentVersionDetails(BaseModel):
    agent_version: AgentVersionNew
    execution: ExecutionNew

class QueueInfo(BaseModel):
    validator_hotkey: str
    place_in_queue: int

class RunningAgentEval(BaseModel):
    version_id: str
    validator_hotkey: str
    started_at: datetime
    agent_id: str
    version_num: int
    miner_hotkey: str
    name: str

class DashboardStats(BaseModel):
    number_of_agents: int
    agent_iterations_last_24_hours: int
    top_agent_score: float
    daily_score_improvement: float