from uuid import UUID
from typing import List
from pydantic import BaseModel



class InferenceMessage(BaseModel):
    role: str
    content: str

class InferenceRequest(BaseModel):
    # TODO
    # evaluation_run_id: UUID
    run_id: UUID
    model: str
    temperature: float
    messages: List[InferenceMessage]



class EmbeddingRequest(BaseModel):
    # TODO
    # evaluation_run_id: UUID
    run_id: UUID
    input: str