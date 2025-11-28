import httpx
import utils.logger as logger
import inference_gateway.config as config

from time import time
from pydantic import BaseModel
from typing import List, Optional
from openai import AsyncOpenAI, APIStatusError
from inference_gateway.providers.provider import Provider
from inference_gateway.models import InferenceTool, EmbeddingResult, InferenceResult, InferenceMessage, InferenceToolMode, EmbeddingModelInfo, InferenceModelInfo, EmbeddingModelPricingMode, inference_tools_to_openai_tools, inference_tool_mode_to_openai_tool_choice, openai_tool_calls_to_inference_tool_calls



if config.USE_CHUTES:
    CHUTES_INFERENCE_MODELS_URL = f"{config.CHUTES_BASE_URL}/models" # https://llm.chutes.ai/v1/models
    CHUTES_EMBEDDING_MODELS_URL = "https://api.chutes.ai/chutes/?template=embedding" # TODO ADAM



class WhitelistedChutesModel(BaseModel):
    name: str
    chutes_name: str = None

    def __init__(self, **data):
        super().__init__(**data)
        if self.chutes_name is None:
            self.chutes_name = self.name

if config.USE_CHUTES:
    WHITELISTED_CHUTES_INFERENCE_MODELS = [
        WhitelistedChutesModel(name="Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8"),
        WhitelistedChutesModel(name="zai-org/GLM-4.5-FP8", chutes_name="zai-org/GLM-4.5"),
        WhitelistedChutesModel(name="deepseek-ai/DeepSeek-V3-0324"),
        WhitelistedChutesModel(name="moonshotai/Kimi-K2-Instruct", chutes_name="moonshotai/Kimi-K2-Instruct-0905"),
        WhitelistedChutesModel(name="zai-org/GLM-4.6-FP8", chutes_name="zai-org/GLM-4.6")
    ]

WHITELISTED_CHUTES_EMBEDDING_MODELS = [
    # TODO ADAM
    # WhitelistedChutesModel(name="Qwen/Qwen3-Embedding-8B")
]



class ChutesProvider(Provider):
    chutes_client: AsyncOpenAI = None


    
    async def init(self) -> "ChutesProvider":
        self.name = "Chutes"



        # NOTE ADAM: curl -s https://llm.chutes.ai/v1/models | jq '.data[] | select(.id == "Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8")'
        # NOTE ADAM: curl -s https://llm.chutes.ai/v1/models | jq '.data[] | select(.id == "Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8") | .pricing'

        # Fetch Chutes inference models
        logger.info(f"Fetching {CHUTES_INFERENCE_MODELS_URL}...")
        async with httpx.AsyncClient() as client:
            chutes_inference_models_response = await client.get(CHUTES_INFERENCE_MODELS_URL)
        chutes_inference_models_response.raise_for_status()
        chutes_inference_models_response = chutes_inference_models_response.json()["data"]
        logger.info(f"Fetched {CHUTES_INFERENCE_MODELS_URL}")



        # Add whitelisted inference models
        for whitelisted_chutes_model in WHITELISTED_CHUTES_INFERENCE_MODELS:     
            chutes_model = next((chutes_model for chutes_model in chutes_inference_models_response if chutes_model["id"] == whitelisted_chutes_model.chutes_name), None)
            if not chutes_model:
                logger.fatal(f"Whitelisted Chutes inference model {whitelisted_chutes_model.chutes_name} is not supported by Chutes")

            if not "text" in chutes_model["input_modalities"]:
                logger.fatal(f"Whitelisted Chutes inference model {whitelisted_chutes_model.chutes_name} does not support text input")
            if not "text" in chutes_model["output_modalities"]:
                logger.fatal(f"Whitelisted Chutes inference model {whitelisted_chutes_model.chutes_name} does not support text output")

            chutes_model_pricing = chutes_model["pricing"]
            max_input_tokens = chutes_model["context_length"]
            cost_usd_per_million_input_tokens = chutes_model_pricing["prompt"]
            cost_usd_per_million_output_tokens = chutes_model_pricing["completion"]

            self.inference_models.append(InferenceModelInfo(
                name=whitelisted_chutes_model.name,
                external_name=whitelisted_chutes_model.chutes_name,
                max_input_tokens=max_input_tokens,
                cost_usd_per_million_input_tokens=cost_usd_per_million_input_tokens,
                cost_usd_per_million_output_tokens=cost_usd_per_million_output_tokens
            ))

            logger.info(f"Found whitelisted Chutes inference model {whitelisted_chutes_model.name}:")
            logger.info(f"  Max input tokens: {max_input_tokens}")
            logger.info(f"  Input cost (USD per million tokens): {cost_usd_per_million_input_tokens}")
            logger.info(f"  Output cost (USD per million tokens): {cost_usd_per_million_output_tokens}")



        # NOTE ADAM: curl -s https://api.chutes.ai/chutes/?template=embedding | jq '.items[] | select(.name == "Qwen/Qwen3-Embedding-8B")'

        # Fetch Chutes embedding models
        logger.info(f"Fetching {CHUTES_EMBEDDING_MODELS_URL}...")
        async with httpx.AsyncClient() as client:
            chutes_embedding_models_response = await client.get(CHUTES_EMBEDDING_MODELS_URL)
        chutes_embedding_models_response.raise_for_status()
        chutes_embedding_models_response = chutes_embedding_models_response.json()["items"]
        logger.info(f"Fetched {CHUTES_EMBEDDING_MODELS_URL}")

        # Add whitelisted embedding models
        for whitelisted_chutes_model in WHITELISTED_CHUTES_EMBEDDING_MODELS:     
            chutes_model = next((chutes_model for chutes_model in chutes_embedding_models_response if chutes_model["name"] == whitelisted_chutes_model.chutes_name), None)
            if not chutes_model:
                logger.fatal(f"Whitelisted Chutes embedding model {whitelisted_chutes_model.chutes_name} is not supported by Chutes")

            max_input_tokens = 40960 # TODO ADAM
            cost_usd_per_second = chutes_model["current_estimated_price"]["usd"]["second"]

            self.embedding_models.append(EmbeddingModelInfo(
                name=whitelisted_chutes_model.name,
                external_name=whitelisted_chutes_model.chutes_name,
                max_input_tokens=max_input_tokens,
                pricing_mode=EmbeddingModelPricingMode.PER_SECOND,
                cost_usd_per_second=cost_usd_per_second
            ))

            logger.info(f"Found whitelisted Chutes inference model {whitelisted_chutes_model.name}:")
            logger.info(f"  Max input tokens: {max_input_tokens}")
            logger.info(f"  Input cost (USD per second): {cost_usd_per_second}")



        self.chutes_client = AsyncOpenAI(
            base_url=config.CHUTES_BASE_URL,
            api_key=config.CHUTES_API_KEY
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
            chat_completion = await self.chutes_client.chat.completions.create(
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
            # Chutes returned 4xx or 5xx
            return InferenceResult(
                status_code=e.status_code,
                error_message=e.response.text
            )

        except Exception as e:
            return InferenceResult(
                status_code=-1,
                error_message=f"Error in ChutesProvider._inference(): {type(e).__name__}: {str(e)}"
            )



    async def _embedding(
        self,
        *,
        model_info: EmbeddingModelInfo,
        input: str
    ) -> EmbeddingResult:
        try:
            start_time = time()
            create_embedding_response = await self.chutes_client.embeddings.create(
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
            # Chutes returned 4xx or 5xx
            return EmbeddingResult(
                status_code=e.status_code,
                error_message=e.response.text
            )

        except Exception as e:
            return EmbeddingResult(
                status_code=-1,
                error_message=f"Error in ChutesProvider._embedding(): {type(e).__name__}: {str(e)}"
            )