from uuid import UUID
from pydantic import BaseModel
from typing import List, Optional



class ModelInfo(BaseModel):
    name: str
    external_name: str
    max_input_tokens: int
    cost_usd_per_million_input_tokens: float
    cost_usd_per_million_output_tokens: float

    def get_cost_usd(self, num_input_tokens: int, num_output_tokens: int) -> float:
        return (num_input_tokens / 1000000) * self.cost_usd_per_million_input_tokens + \
               (num_output_tokens / 1000000) * self.cost_usd_per_million_output_tokens



class InferenceResult(BaseModel):
    status_code: int
    response: str
    num_input_tokens: Optional[int] = None
    num_output_tokens: Optional[int] = None
    cost_usd: Optional[float] = None



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