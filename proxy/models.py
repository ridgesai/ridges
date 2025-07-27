from enum import Enum
from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime

from pydantic import BaseModel, Field
from api.src.backend.entities import SandboxStatus

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
    run_id: Optional[str] = Field(None, description="Evaluation run ID")

class InferenceRequest(BaseModel):
    """Model for inference request"""
    run_id: Optional[str] = Field(None, description="Evaluation run ID")
    model: Optional[str] = Field(None, description="Model to use for inference")
    temperature: Optional[float] = Field(None, description="Temperature for inference")
    messages: List[GPTMessage] = Field(..., description="Messages to send to the model")

class Embedding(BaseModel):
    """Model for embedding data stored in database"""
    id: Optional[UUID] = Field(None, description="Embedding ID")
    run_id: UUID = Field(..., description="Evaluation run ID")
    input_text: str = Field(..., description="Text that was embedded")
    cost: Optional[float] = Field(None, description="Cost of the embedding")
    response: Optional[Dict[str, Any]] = Field(None, description="Response from the embedding API")
    created_at: datetime = Field(..., description="When the embedding was created")
    finished_at: Optional[datetime] = Field(None, description="When the embedding was completed")

class Inference(BaseModel):
    """Model for inference data stored in database"""
    id: Optional[UUID] = Field(None, description="Inference ID")
    run_id: UUID = Field(..., description="Evaluation run ID")
    messages: List[Dict[str, str]] = Field(..., description="Messages sent to the model")
    temperature: float = Field(..., description="Temperature used for inference")
    model: str = Field(..., description="Model used for inference")
    provider: Optional[str] = Field(None, description="AI provider used (Chutes, Targon)")
    cost: Optional[float] = Field(None, description="Cost of the inference")
    response: Optional[str] = Field(None, description="Response from the inference API")
    total_tokens: Optional[int] = Field(None, description="Total tokens used")
    created_at: datetime = Field(..., description="When the inference was created")
    finished_at: Optional[datetime] = Field(None, description="When the inference was completed")

 