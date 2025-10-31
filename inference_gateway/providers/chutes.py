import httpx
import utils.logger as logger
import inference_gateway.config as config

from typing import List
from pydantic import BaseModel
from openai import AsyncOpenAI, APIStatusError
from inference_gateway.providers.provider import Provider
from inference_gateway.models import ModelInfo, InferenceResult, InferenceMessage



if config.USE_CHUTES:
    CHUTES_MODELS_URL = f"{config.CHUTES_BASE_URL}/models"



class WhitelistedChutesModel(BaseModel):
    name: str
    chutes_name: str = None

    def __init__(self, **data):
        super().__init__(**data)
        if self.chutes_name is None:
            self.chutes_name = self.name

WHITELISTED_CHUTES_INFERENCE_MODELS = [
    # WhitelistedChutesModel(name="Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8"),
    # WhitelistedChutesModel(name="zai-org/GLM-4.5-FP8"),
    # WhitelistedChutesModel(name="deepseek-ai/DeepSeek-V3-0324"),
    WhitelistedChutesModel(name="moonshotai/Kimi-K2-Instruct", chutes_name="moonshotai/Kimi-K2-Instruct-0905")
    # WhitelistedChutesModel(name="zai-org/GLM-4.6-FP8")
]

WHITELISTED_CHUTES_EMBEDDING_MODELS = [
    # TODO
]



class ChutesProvider(Provider):
    chutes_client: AsyncOpenAI = None


    
    async def init(self) -> "ChutesProvider":
        self.name = "Chutes"



        # NOTE ADAM: curl -s https://llm.chutes.ai/v1/models | jq '.data[] | select(.id == "Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8")'
        # NOTE ADAM: curl -s https://llm.chutes.ai/v1/models | jq '.data[] | select(.id == "Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8") | .pricing'

        # Fetch Chutes models
        logger.info(f"Fetching {CHUTES_MODELS_URL}...")
        async with httpx.AsyncClient() as client:
            chutes_models_response = await client.get(CHUTES_MODELS_URL)
        chutes_models_response.raise_for_status()
        chutes_models = chutes_models_response.json()["data"]
        logger.info(f"Fetched {CHUTES_MODELS_URL}")



        # Add whitelisted inference models
        for whitelisted_chutes_model in WHITELISTED_CHUTES_INFERENCE_MODELS:     
            chutes_model = next((chutes_model for chutes_model in chutes_models if chutes_model["id"] == whitelisted_chutes_model.chutes_name), None)
            if not chutes_model:
                logger.fatal(f"Whitelisted Chutes inference model {whitelisted_chutes_model.chutes_name} is not supported by Chutes")

            chutes_model_pricing = chutes_model["pricing"]
            max_input_tokens = chutes_model["context_length"]
            cost_usd_per_million_input_tokens = chutes_model_pricing["prompt"]
            cost_usd_per_million_output_tokens = chutes_model_pricing["completion"]

            self.inference_models.append(ModelInfo(
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

        # TODO ADAM: embedding



        self.chutes_client = AsyncOpenAI(
            base_url=config.CHUTES_BASE_URL,
            api_key=config.CHUTES_API_KEY
        )



        return self
        


    async def _inference(self, model_info: ModelInfo, temperature: float, messages: List[InferenceMessage]) -> InferenceResult:
        try:
            chat_completion = await self.chutes_client.chat.completions.create(
                model=model_info.external_name,
                temperature=temperature,
                messages=messages,
                stream=False
            )
            
            response = chat_completion.choices[0].message.content

            num_input_tokens = chat_completion.usage.prompt_tokens
            num_output_tokens = chat_completion.usage.completion_tokens
            cost_usd = model_info.get_cost_usd(num_input_tokens, num_output_tokens) if model_info else None

            return InferenceResult(
                status_code=200,
                response=response,
                num_input_tokens=num_input_tokens,
                num_output_tokens=num_output_tokens,
                cost_usd=cost_usd
            )

        except APIStatusError as e:
            # Chutes returned 4xx or 5xx
            return InferenceResult(
                status_code=e.status_code,
                response=e.response.text
            )

        except Exception as e:
            return InferenceResult(
                status_code=-1,
                response=f"Error in ChutesProvider._inference(): {type(e).__name__}: {str(e)}"
            )



    async def _embedding(self, model_info: ModelInfo, input: str) -> List[float]:
        # TODO ADAM
        pass