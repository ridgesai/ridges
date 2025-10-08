from uuid import UUID
from typing import List
from pydantic import BaseModel



class InferenceMessage(BaseModel):
    role: str
    content: str

class InferenceRequest(BaseModel):
    evaluation_run_id: UUID
    model: str
    temperature: float
    messages: List[InferenceMessage]

class InferenceResponse(BaseModel):
    response: str



class EmbeddingRequest(BaseModel):
    evaluation_run_id: UUID
    input: str

class EmbeddingResponse(BaseModel):
    embedding: List[float]