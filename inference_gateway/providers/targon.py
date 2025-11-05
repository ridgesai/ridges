import httpx
import utils.logger as logger
import inference_gateway.config as config

from typing import List
from pydantic import BaseModel
from openai import AsyncOpenAI, APIStatusError
from inference_gateway.providers.provider import Provider
from inference_gateway.models import ModelInfo, InferenceResult, InferenceMessage



if config.USE_TARGON:
    TARGON_MODELS_URL = f"{config.TARGON_BASE_URL}/models"



class WhitelistedTargonModel(BaseModel):
    name: str
    targon_name: str = None

    def __init__(self, **data):
        super().__init__(**data)
        if self.targon_name is None:
            self.targon_name = self.name

WHITELISTED_TARGON_INFERENCE_MODELS = [
    WhitelistedTargonModel(name="Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8"),
    WhitelistedTargonModel(name="zai-org/GLM-4.5-FP8", targon_name="zai-org/GLM-4.5"),
    WhitelistedTargonModel(name="deepseek-ai/DeepSeek-V3-0324", targon_name="deepseek-ai/DeepSeek-V3"),
    WhitelistedTargonModel(name="zai-org/GLM-4.6-FP8")
]

WHITELISTED_TARGON_EMBEDDING_MODELS = [
    # TODO
]



class TargonProvider(Provider):
    targon_client: AsyncOpenAI = None


    
    async def init(self) -> "TargonProvider":
        self.name = "Targon"


        
        # Fetch Targon models
        logger.info(f"Fetching {TARGON_MODELS_URL}...")
        async with httpx.AsyncClient() as client:
            targon_models_response = await client.get(TARGON_MODELS_URL, headers={"Authorization": f"Bearer {config.TARGON_API_KEY}"})
        targon_models_response.raise_for_status()
        targon_models = targon_models_response.json()["data"]
        logger.info(f"Fetched {TARGON_MODELS_URL}")



        # Add whitelisted inference models
        for whitelisted_targon_model in WHITELISTED_TARGON_INFERENCE_MODELS:
            targon_model = next((targon_model for targon_model in targon_models if targon_model["id"] == whitelisted_targon_model.targon_name), None)
            if not targon_model:
                logger.fatal(f"Whitelisted Targon inference model {whitelisted_targon_model.targon_name} is not supported by Targon")

            targon_model_pricing = targon_model["pricing"]
            max_input_tokens = targon_model["context_length"]
            cost_usd_per_million_input_tokens = float(targon_model_pricing["prompt"]) * 1_000_000
            cost_usd_per_million_output_tokens = float(targon_model_pricing["completion"]) * 1_000_000

            self.inference_models.append(ModelInfo(
                name=whitelisted_targon_model.name,
                external_name=whitelisted_targon_model.targon_name,
                max_input_tokens=max_input_tokens,
                cost_usd_per_million_input_tokens=cost_usd_per_million_input_tokens,
                cost_usd_per_million_output_tokens=cost_usd_per_million_output_tokens
            ))

            logger.info(f"Found whitelisted Targon inference model {whitelisted_targon_model.name}:")
            logger.info(f"  Max input tokens: {max_input_tokens}")
            logger.info(f"  Input cost (USD per million tokens): {cost_usd_per_million_input_tokens}")
            logger.info(f"  Output cost (USD per million tokens): {cost_usd_per_million_output_tokens}")

        # TODO ADAM: embedding



        self.targon_client = AsyncOpenAI(
            base_url=config.TARGON_BASE_URL,
            api_key=config.TARGON_API_KEY
        )



        return self
        


    async def _inference(self, model_info: ModelInfo, temperature: float, messages: List[InferenceMessage]) -> InferenceResult:
        try:
            chat_completion = await self.targon_client.chat.completions.create(
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
            # Targon returned 4xx or 5xx
            return InferenceResult(
                status_code=e.status_code,
                response=e.response.text
            )

        except Exception as e:
            return InferenceResult(
                status_code=-1,
                response=f"Error in TargonProvider._inference(): {type(e).__name__}: {str(e)}"
            )



    async def _embedding(self, model_info: ModelInfo, input: str) -> List[float]:
        # TODO ADAM
        pass