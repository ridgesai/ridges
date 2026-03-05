import httpx
import utils.logger as logger
import inference_gateway.config as config

from time import time
from pydantic import BaseModel
from typing import List, Optional
from openai import AsyncOpenAI, APIStatusError
from inference_gateway.providers.provider import Provider
from inference_gateway.models import InferenceTool, EmbeddingResult, InferenceResult, InferenceMessage, InferenceToolMode, EmbeddingModelInfo, InferenceModelInfo, inference_tools_to_openai_tools, inference_tool_mode_to_openai_tool_choice, openai_tool_calls_to_inference_tool_calls



if config.USE_SGLANG:
    SGLANG_MODELS_URL = f"{config.SGLANG_BASE_URL}/models"



class WhitelistedSGLangModel(BaseModel):
    name: str
    sglang_name: str = None

    def __init__(self, **data):
        super().__init__(**data)
        if self.sglang_name is None:
            self.sglang_name = self.name

WHITELISTED_SGLANG_INFERENCE_MODELS = [
    WhitelistedSGLangModel(name="Qwen/Qwen3-Coder-Next-FP8"),
    WhitelistedSGLangModel(name="Qwen/Qwen3-Coder-Next", sglang_name="Qwen/Qwen3-Coder-Next-FP8"),
]

WHITELISTED_SGLANG_EMBEDDING_MODELS = [
]

CONTEXT_LENGTHS = {
    "Qwen/Qwen3-Coder-Next-FP8": 256_000,
}

PRICING = {
    "Qwen/Qwen3-Coder-Next-FP8": (0.12, 0.75),
}

SGLANG_MODELS = [
    {
        "id": "Qwen/Qwen3-Coder-Next-FP8",
        "name": "Qwen/Qwen3-Coder-Next-FP8",
        "context_length": 256_000, # can reduce if OOM
        "pricing": (0.12, 0.75), # in/out USD per million tokens
    },
]


class SglangProvider(Provider):  
    async def init(self) -> "SglangProvider":
        self.name = "SGLang"


        
        # Fetch SGLang models
        # logger.info(f"Fetching {SGLANG_MODELS_URL}...")
        # async with httpx.AsyncClient() as client:
        #     sglang_models_response = await client.get(SGLANG_MODELS_URL, headers={"Authorization": f"Bearer {config.SGLANG_API_KEY}"})
        # sglang_models_response.raise_for_status()
        # sglang_models = sglang_models_response.json()["data"]
        # logger.info(f"Fetched {SGLANG_MODELS_URL}")

        sglang_models = SGLANG_MODELS

        # Add whitelisted inference models
        for whitelisted_sglang_model in WHITELISTED_SGLANG_INFERENCE_MODELS:
            sglang_model = next((sglang_model for sglang_model in sglang_models if sglang_model["id"] == whitelisted_sglang_model.sglang_name), None)
            if not sglang_model:
                logger.fatal(f"Whitelisted SGLang inference model {whitelisted_sglang_model.sglang_name} is not supported by SGLang server")

            sglang_model_pricing = sglang_model["pricing"]
            max_input_tokens = sglang_model["context_length"]
            cost_usd_per_million_input_tokens = sglang_model_pricing[0]
            cost_usd_per_million_output_tokens = sglang_model_pricing[1]

            self.inference_models.append(InferenceModelInfo(
                name=whitelisted_sglang_model.name,
                external_name=whitelisted_sglang_model.sglang_name,
                max_input_tokens=max_input_tokens,
                cost_usd_per_million_input_tokens=cost_usd_per_million_input_tokens,
                cost_usd_per_million_output_tokens=cost_usd_per_million_output_tokens
            ))

            logger.info(f"Found whitelisted SGLang inference model {whitelisted_sglang_model.name}:")
            logger.info(f"  Max input tokens: {max_input_tokens}")
            logger.info(f"  Input cost (USD per million tokens): {cost_usd_per_million_input_tokens}")
            logger.info(f"  Output cost (USD per million tokens): {cost_usd_per_million_output_tokens}")


        self.sglang_client = AsyncOpenAI(
            base_url=config.SGLANG_BASE_URL,
            api_key=config.SGLANG_API_KEY
        )

        return self
        


    async def _inference(
        self,
        *,
        model_info: InferenceModelInfo,
        temperature: float,
        messages: List[InferenceMessage],
        tool_mode: InferenceToolMode,
        tools: Optional[List[InferenceTool]]
    ) -> InferenceResult:
        try:
            chat_completion = await self.sglang_client.chat.completions.create(
                model=model_info.external_name,
                temperature=temperature,
                messages=messages,
                tool_choice=inference_tool_mode_to_openai_tool_choice(tool_mode),
                tools=inference_tools_to_openai_tools(tools) if tools else None,
                stream=False
            )

            message = chat_completion.choices[0].message

            num_input_tokens = chat_completion.usage.prompt_tokens
            num_output_tokens = chat_completion.usage.completion_tokens
            cost_usd = model_info.get_cost_usd(num_input_tokens, num_output_tokens)

            return InferenceResult(
                status_code=200,

                content=message.content if message.content else "",
                tool_calls=openai_tool_calls_to_inference_tool_calls(message.tool_calls) if message.tool_calls else [],

                num_input_tokens=num_input_tokens,
                num_output_tokens=num_output_tokens,
                cost_usd=cost_usd
            )
    
        except APIStatusError as e:
            # SGLang returned 4xx or 5xx
            return InferenceResult(
                status_code=e.status_code,
                error_message=e.response.text
            )

        except Exception as e:
            return InferenceResult(
                status_code=-1,
                error_message=f"Error in SGLangProvider._inference(): {type(e).__name__}: {str(e)}"
            )



    async def _embedding(
        self,
        *,
        model_info: EmbeddingModelInfo,
        input: str
    ) -> EmbeddingResult:
        try:
            start_time = time()
            create_embedding_response = await self.sglang_client.embeddings.create(
                model=model_info.external_name,
                input=input
            )
            end_time = time()

            embedding = create_embedding_response.data[0].embedding

            num_input_tokens = create_embedding_response.usage.prompt_tokens
            cost_usd = model_info.get_cost_usd(num_input_tokens, end_time - start_time)

            return EmbeddingResult(
                status_code=200,
                
                embedding=embedding,
                
                num_input_tokens=num_input_tokens,
                cost_usd=cost_usd
            )

        except APIStatusError as e:
            # SGLang returned 4xx or 5xx
            return EmbeddingResult(
                status_code=e.status_code,
                error_message=e.response.text
            )

        except Exception as e:
            return EmbeddingResult(
                status_code=-1,
                error_message=f"Error in SGLangProvider._embedding(): {type(e).__name__}: {str(e)}"
            )