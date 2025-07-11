from enum import Enum
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field

class SandboxStatus(str, Enum):
    """Enum for sandbox status values"""
    started = "started"
    sandbox_created = "sandbox_created"
    patch_generated = "patch_generated"
    eval_started = "eval_started"
    result_scored = "result_scored"

class EvaluationRun(BaseModel):
    """Model for evaluation run data"""
    run_id: UUID
    status: SandboxStatus

class GPTMessage(BaseModel):
    """Model for GPT message structure"""
    role: str = Field(..., description="Role of the message (user, assistant, system)")
    content: str = Field(..., description="Content of the message")

class EmbeddingRequest(BaseModel):
    """Model for embedding request"""
    input: str = Field(..., description="Text to embed")
    run_id: str = Field(..., description="Evaluation run ID")

class InferenceRequest(BaseModel):
    """Model for inference request"""
    run_id: str = Field(..., description="Evaluation run ID")
    model: Optional[str] = Field(None, description="Model to use for inference")
    temperature: Optional[float] = Field(None, description="Temperature for inference")
    messages: List[GPTMessage] = Field(..., description="Messages to send to the model")

 