from enum import Enum
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

# TODO ADAM
class InferenceToolParameterType(Enum):
    string = "string"
    integer = "integer"
    float = "float"
    boolean = "boolean"
class InferenceToolParameter(BaseModel):
    name: str
    type: InferenceToolParameterType
    description: str
    required: bool
class InferenceTool(BaseModel):
    name: str
    description: str
    parameters: List[InferenceToolParameter]

class InferenceToolMode(Enum):
    auto = "auto"
    required = "required"
    none = "none"
class InferenceRequest(BaseModel):
    run_id: UUID # TODO ADAM: evaluation_run_id: UUID
    model: str
    temperature: float
    messages: List[InferenceMessage]
    # tools: Optional[List[InferenceTool]] = None
    # tool_mode: Optional[InferenceToolMode] = InferenceToolMode.auto



class EmbeddingRequest(BaseModel):
    run_id: UUID # TODO ADAM: evaluation_run_id: UUID
    input: str