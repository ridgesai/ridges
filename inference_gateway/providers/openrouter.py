from collections import defaultdict
from types import SimpleNamespace
import httpx
import utils.logger as logger
import inference_gateway.config as config

from time import time
from pydantic import BaseModel
from typing import List, Optional
from openai import AsyncOpenAI, APIStatusError, AsyncStream
from inference_gateway.providers.provider import Provider
from inference_gateway.models import InferenceTool, EmbeddingResult, InferenceResult, InferenceMessage, InferenceToolMode, EmbeddingModelInfo, InferenceModelInfo, EmbeddingModelPricingMode, inference_tools_to_openai_tools, inference_tool_mode_to_openai_tool_choice, openai_tool_calls_to_inference_tool_calls


if config.USE_OPENROUTER:
    OPENROUTER_INFERENCE_MODELS_URL = f"{config.OPENROUTER_BASE_URL}/models" # https://openrouter.ai/api/v1/models


class WhitelistedOpenRouterModel(BaseModel):
    name: str
    openrouter_name: str = None

    def __init__(self, **data):
        super().__init__(**data)
        if self.openrouter_name is None:
            self.openrouter_name = self.name

if config.USE_OPENROUTER:
    WHITELISTED_OPENROUTER_INFERENCE_MODELS = [
        WhitelistedOpenRouterModel(name="deepseek-ai/DeepSeek-R1-0528", openrouter_name="deepseek/deepseek-r1-0528"),
        WhitelistedOpenRouterModel(name="zai-org/GLM-4.6", openrouter_name="z-ai/glm-4.6"),
        WhitelistedOpenRouterModel(name="zai-org/GLM-4.6-FP8", openrouter_name="z-ai/glm-4.6"),
        WhitelistedOpenRouterModel(name="zai-org/GLM-4.7", openrouter_name="z-ai/glm-4.7"),
        WhitelistedOpenRouterModel(name="zai-org/GLM-4.7-FP8", openrouter_name="z-ai/glm-4.7"),
        WhitelistedOpenRouterModel(name="zai-org/GLM-5-FP8", openrouter_name="z-ai/glm-5"),
        WhitelistedOpenRouterModel(name="Qwen/Qwen3-Coder-Next", openrouter_name="qwen/qwen3-coder-next"),
        WhitelistedOpenRouterModel(name="Qwen/Qwen3.5-397B-A17B", openrouter_name="qwen/qwen3.5-397b-a17b"),
        WhitelistedOpenRouterModel(name="moonshotai/Kimi-K2.5", openrouter_name="moonshotai/kimi-k2.5"),
        WhitelistedOpenRouterModel(name="MiniMaxAI/MiniMax-M2.5", openrouter_name="minimax/minimax-m2.5"),
    ]

WHITELISTED_OPENROUTER_EMBEDDING_MODELS = [
    WhitelistedOpenRouterModel(name="Qwen/Qwen3-Embedding-8B", openrouter_name="qwen/qwen3-embedding-8b")
]


class OpenRouterProvider(Provider):
    async def init(self) -> "OpenRouterProvider":
        self.name = "OpenRouter"

        # Fetch OpenRouter inference models
        logger.info(f"Fetching {OPENROUTER_INFERENCE_MODELS_URL}...")
        async with httpx.AsyncClient() as client:
            openrouter_inference_models_response = await client.get(OPENROUTER_INFERENCE_MODELS_URL)
        openrouter_inference_models_response.raise_for_status()
        openrouter_inference_models_response = openrouter_inference_models_response.json()["data"]
        logger.info(f"Fetched {OPENROUTER_INFERENCE_MODELS_URL}")

        # Add whitelisted inference models
        for whitelisted_openrouter_model in WHITELISTED_OPENROUTER_INFERENCE_MODELS:     
            openrouter_model = next((openrouter_model for openrouter_model in openrouter_inference_models_response if openrouter_model["id"] == whitelisted_openrouter_model.openrouter_name), None)
            if not openrouter_model:
                logger.fatal(f"Whitelisted OpenRouter inference model {whitelisted_openrouter_model.openrouter_name} is not supported by OpenRouter")

            if not "text" in openrouter_model["architecture"]["input_modalities"]:
                logger.fatal(f"Whitelisted OpenRouter inference model {whitelisted_openrouter_model.chutes_name} does not support text input")
            if not "text" in openrouter_model["architecture"]["output_modalities"]:
                logger.fatal(f"Whitelisted OpenRouter inference model {whitelisted_openrouter_model.chutes_name} does not support text output")

            openrouter_model_pricing = openrouter_model["pricing"]
            max_input_tokens = openrouter_model["context_length"]
            cost_usd_per_million_input_tokens = float(openrouter_model_pricing["prompt"])
            cost_usd_per_million_output_tokens = float(openrouter_model_pricing["completion"])

            self.inference_models.append(InferenceModelInfo(
                name=whitelisted_openrouter_model.name,
                external_name=whitelisted_openrouter_model.openrouter_name,
                max_input_tokens=max_input_tokens,
                cost_usd_per_million_input_tokens=cost_usd_per_million_input_tokens,
                cost_usd_per_million_output_tokens=cost_usd_per_million_output_tokens
            ))

            logger.info(f"Found whitelisted OpenRouter inference model {whitelisted_openrouter_model.name}:")
            logger.info(f"  Max input tokens: {max_input_tokens}")
            logger.info(f"  Input cost (USD per million tokens): {cost_usd_per_million_input_tokens}")
            logger.info(f"  Output cost (USD per million tokens): {cost_usd_per_million_output_tokens}")


        # Add whitelisted embedding models
        for whitelisted_openrouter_model in WHITELISTED_OPENROUTER_EMBEDDING_MODELS:
            openrouter_model = next((openrouter_model for openrouter_model in openrouter_inference_models_response if openrouter_model["id"] == whitelisted_openrouter_model.openrouter_name), None)
            if not openrouter_model:
                logger.fatal(f"Whitelisted OpenRouter embedding model {whitelisted_openrouter_model.openrouter_name} is not supported by OpenRouter")

            if not "text" in openrouter_model["input_modalities"]:
                logger.fatal(f"Whitelisted OpenRouter embedding model {whitelisted_openrouter_model.openrouter_name} does not support text input")
            if not "embedding" in openrouter_model["output_modalities"]:
                logger.fatal(f"Whitelisted OpenRouter embedding model {whitelisted_openrouter_model.openrouter_name} does not support embedding output")

            if not "EMBEDDING" in openrouter_model["supported_endpoints"]:
                logger.fatal(f"Whitelisted OpenRouter embedding model {whitelisted_openrouter_model.openrouter_name} does not support embedding endpoints")

            max_input_tokens = openrouter_model["context_length"]
            cost_usd_per_million_input_tokens = float(openrouter_model["pricing"]["prompt"])

            self.embedding_models.append(EmbeddingModelInfo(
                name=whitelisted_openrouter_model.name,
                external_name=whitelisted_openrouter_model.openrouter_name,
                max_input_tokens=max_input_tokens,
                cost_usd_per_million_input_tokens=cost_usd_per_million_input_tokens
            ))

            logger.info(f"Found whitelisted OpenRouter embedding model {whitelisted_openrouter_model.name}:")
            logger.info(f"  Max input tokens: {max_input_tokens}")
            logger.info(f"  Input cost (USD per million tokens): {cost_usd_per_million_input_tokens}")


        self.openrouter_client = AsyncOpenAI(
            base_url=config.OPENROUTER_BASE_URL,
            api_key=config.OPENROUTER_API_KEY,
            default_headers={ # Optional. For rankings on openrouter.ai.
                'HTTP-Referer': 'https://ridges.ai',
                'X-Title': 'Ridges'
            }
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
            completion_stream: AsyncStream = await self.openrouter_client.chat.completions.create(
                model=model_info.external_name,
                temperature=temperature,
                messages=messages,
                tool_choice=inference_tool_mode_to_openai_tool_choice(tool_mode),
                tools=inference_tools_to_openai_tools(tools) if tools else None,
                stream=True,
                stream_options={"include_usage": True}
            )
            streamed_completion = []
            tool_calls = dict()
            async for chunk in completion_stream:
                if len(chunk.choices) > 0:
                    chunk_delta = chunk.choices[0].delta
                    chunk_content = chunk_delta.content
                    streamed_completion.append(chunk_content if chunk_content else "")

                    chunk_tool_calls = chunk_delta.tool_calls
                    if chunk_tool_calls is not None:
                        # Tool calls will be in chunks too, so we concat them
                        for tool_call_chunk in chunk_tool_calls:
                            if tool_call_chunk.index not in tool_calls:
                                tool_calls[tool_call_chunk.index] = SimpleNamespace(
                                    id="", type=tool_call_chunk.type, function=SimpleNamespace(name="", arguments="")
                                )
                            tool_call = tool_calls[tool_call_chunk.index]

                            if tool_call_chunk.id is not None:
                                tool_call.id += tool_call_chunk.id
                            if tool_call_chunk.function.name is not None:
                                tool_call.function.name += tool_call_chunk.function.name
                            if tool_call_chunk.function.arguments is not None:
                                tool_call.function.arguments += tool_call_chunk.function.arguments

                # last chunk has no choices

            last_chunk = chunk

            message_content = "".join(streamed_completion)
            message_tool_calls = [
                tool_calls[idx] for idx in sorted(tool_calls) # sort by index
            ]

            num_input_tokens = last_chunk.usage.prompt_tokens
            num_output_tokens = last_chunk.usage.completion_tokens
            cost_usd = model_info.get_cost_usd(num_input_tokens, num_output_tokens)

            return InferenceResult(
                status_code=200,

                content=message_content,
                tool_calls=openai_tool_calls_to_inference_tool_calls(message_tool_calls) if message_tool_calls else [],

                num_input_tokens=num_input_tokens,
                num_output_tokens=num_output_tokens,
                cost_usd=cost_usd
            )

        except APIStatusError as e:
            # Chutes returned 4xx or 5xx
            return InferenceResult(
                status_code=e.status_code,
                error_message=e.response.text
            )

        except Exception as e:
            return InferenceResult(
                status_code=-1,
                error_message=f"Error in OpenRouterProvider._inference(): {type(e).__name__}: {str(e)}"
            )

    async def _embedding(
        self,
        *,
        model_info: EmbeddingModelInfo,
        input: str
    ) -> EmbeddingResult:
        try:
            start_time = time()
            create_embedding_response = await self.openrouter_client.embeddings.create(
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
            # OpenRouter returned 4xx or 5xx
            return EmbeddingResult(
                status_code=e.status_code,
                error_message=e.response.text
            )

        except Exception as e:
            return EmbeddingResult(
                status_code=-1,
                error_message=f"Error in OpenRouterProvider._embedding(): {type(e).__name__}: {str(e)}"
            )
