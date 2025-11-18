import json

from enum import Enum
from uuid import UUID
from pydantic import BaseModel
from typing import Any, List, Optional
from openai.types.chat import ChatCompletionToolChoiceOptionParam
from openai.types.shared_params.function_definition import FunctionDefinition
from openai.types.shared_params.function_parameters import FunctionParameters
from openai.types.chat.chat_completion_tool_param import ChatCompletionToolParam
from openai.types.chat.chat_completion_message_tool_call import ChatCompletionMessageToolCallUnion



# Model Info
class InferenceModelInfo(BaseModel):
    name: str
    external_name: str
    max_input_tokens: int
    cost_usd_per_million_input_tokens: float
    cost_usd_per_million_output_tokens: float

    def get_cost_usd(self, num_input_tokens: int, num_output_tokens: int) -> float:
        return (num_input_tokens / 1_000_000) * self.cost_usd_per_million_input_tokens + \
               (num_output_tokens / 1_000_000) * self.cost_usd_per_million_output_tokens

class EmbeddingModelPricingMode(Enum):
    PER_TOKEN = "per_token"
    PER_SECOND = "per_second"
class EmbeddingModelInfo(BaseModel):
    name: str
    external_name: str
    max_input_tokens: int
    pricing_mode: EmbeddingModelPricingMode = EmbeddingModelPricingMode.PER_TOKEN
    cost_usd_per_million_input_tokens: Optional[float] = None
    cost_usd_per_second: Optional[float] = None

    def get_cost_usd(self, num_input_tokens: int, num_seconds: float) -> float:
        if self.pricing_mode == EmbeddingModelPricingMode.PER_TOKEN:
            return (num_input_tokens / 1_000_000) * self.cost_usd_per_million_input_tokens
        else: # if self.pricing_mode == EmbeddingModelPricingMode.PER_SECOND:
            return num_seconds * self.cost_usd_per_second



# Results
class InferenceToolCallArgument(BaseModel):
    name: str
    value: Any
class InferenceToolCall(BaseModel):
    name: str
    arguments: List[InferenceToolCallArgument]

def openai_tool_calls_to_inference_tool_calls(openai_tool_calls: List[ChatCompletionMessageToolCallUnion]) -> List[InferenceToolCall]:
    inference_tool_calls = []
    
    for openai_tool_call in openai_tool_calls:
        try:
            arguments_dict = json.loads(openai_tool_call.function.arguments)
        except json.JSONDecodeError:
            # TODO ADAM
            arguments_dict = {}
        
        inference_tool_calls.append(InferenceToolCall(
            name=openai_tool_call.function.name,
            arguments=[InferenceToolCallArgument(name=name, value=value) for name, value in arguments_dict.items()]
        ))
    
    return inference_tool_calls

class InferenceResult(BaseModel):
    status_code: int

    # if status_code == 200
    content: Optional[str] = None
    tool_calls: Optional[List[InferenceToolCall]] = None
    # if status_code != 200
    error_message: Optional[str] = None
    
    num_input_tokens: Optional[int] = None
    num_output_tokens: Optional[int] = None
    cost_usd: Optional[float] = None

class EmbeddingResult(BaseModel):
    status_code: int
    
    embedding: Optional[List[float]] = None # if status_code == 200
    error_message: Optional[str] = None # if status_code != 200
    
    num_input_tokens: Optional[int] = None
    cost_usd: Optional[float] = None



# Inference
class InferenceMessage(BaseModel):
    role: str
    content: str



class InferenceToolParameterType(Enum):
    BOOLEAN = "boolean"
    INTEGER = "integer"
    NUMBER = "number"
    STRING = "string"
    ARRAY = "array"
    OBJECT = "object"
    
class InferenceToolParameter(BaseModel):
    type: InferenceToolParameterType
    name: str
    description: str
    required: bool = False

def inference_tool_parameters_to_openai_parameters(parameters: List[InferenceToolParameter]) -> FunctionParameters:
    return {
        "properties": {
            parameter.name: {
                "type": parameter.type.value,
                "description": parameter.description
            } for parameter in parameters
        },
        "required": [parameter.name for parameter in parameters if parameter.required]
    }

class InferenceTool(BaseModel):
    name: str
    description: str
    parameters: List[InferenceToolParameter]

def inference_tools_to_openai_tools(tools: List[InferenceTool]) -> List[ChatCompletionToolParam]:
    return [ChatCompletionToolParam(
        type="function",
        function=FunctionDefinition(
            name=tool.name,
            description=tool.description,
            parameters=inference_tool_parameters_to_openai_parameters(tool.parameters)
        )
    ) for tool in tools]

class InferenceToolMode(Enum):
    NONE = "none"
    AUTO = "auto"
    REQUIRED = "required"

def inference_tool_mode_to_openai_tool_choice(tool_mode: InferenceToolMode) -> ChatCompletionToolChoiceOptionParam:
    return tool_mode.value



# HTTP
class InferenceRequest(BaseModel):
    evaluation_run_id: UUID
    model: str
    temperature: float
    messages: List[InferenceMessage]
    tool_mode: Optional[InferenceToolMode] = InferenceToolMode.NONE
    tools: Optional[List[InferenceTool]] = None

class InferenceResponse(BaseModel):
    content: str
    tool_calls: List[InferenceToolCall]



class EmbeddingRequest(BaseModel):
    evaluation_run_id: UUID
    model: str
    input: str

class EmbeddingResponse(BaseModel):
    embedding: List[float]