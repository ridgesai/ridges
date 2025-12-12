import httpx
import utils.logger as logger
import inference_gateway.config as config

from time import time
from pydantic import BaseModel
from typing import List, Optional
from openai import AsyncOpenAI, APIStatusError
from inference_gateway.providers.provider import Provider
from inference_gateway.models import InferenceTool, EmbeddingResult, InferenceResult, InferenceMessage, InferenceToolMode, EmbeddingModelInfo, InferenceModelInfo, inference_tools_to_openai_tools, inference_tool_mode_to_openai_tool_choice, openai_tool_calls_to_inference_tool_calls, inference_message_to_openai_message



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
    WhitelistedTargonModel(name="deepseek-ai/DeepSeek-V3-0324"),
    WhitelistedTargonModel(name="moonshotai/Kimi-K2-Instruct", targon_name="moonshotai/Kimi-K2-Thinking"),
    WhitelistedTargonModel(name="zai-org/GLM-4.6-FP8", targon_name="zai-org/GLM-4.6")
]

WHITELISTED_TARGON_EMBEDDING_MODELS = [
    WhitelistedTargonModel(name="Qwen/Qwen3-Embedding-8B")
]



class TargonProvider(Provider):  
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

            if not "text" in targon_model["input_modalities"]:
                logger.fatal(f"Whitelisted Targon inference model {whitelisted_targon_model.targon_name} does not support text input")
            if not "text" in targon_model["output_modalities"]:
                logger.fatal(f"Whitelisted Targon inference model {whitelisted_targon_model.targon_name} does not support text output")

            if not "CHAT" in targon_model["supported_endpoints"]:
                logger.fatal(f"Whitelisted Targon inference model {whitelisted_targon_model.targon_name} does not support chat endpoints")
            if not "COMPLETION" in targon_model["supported_endpoints"]:
                logger.fatal(f"Whitelisted Targon inference model {whitelisted_targon_model.targon_name} does not support completion endpoints")

            targon_model_pricing = targon_model["pricing"]
            max_input_tokens = targon_model["context_length"]
            cost_usd_per_million_input_tokens = float(targon_model_pricing["prompt"]) * 1_000_000
            cost_usd_per_million_output_tokens = float(targon_model_pricing["completion"]) * 1_000_000

            self.inference_models.append(InferenceModelInfo(
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



        # Add whitelisted embedding models
        for whitelisted_targon_model in WHITELISTED_TARGON_EMBEDDING_MODELS:
            targon_model = next((targon_model for targon_model in targon_models if targon_model["id"] == whitelisted_targon_model.targon_name), None)
            if not targon_model:
                logger.fatal(f"Whitelisted Targon embedding model {whitelisted_targon_model.targon_name} is not supported by Targon")

            if not "text" in targon_model["input_modalities"]:
                logger.fatal(f"Whitelisted Targon embedding model {whitelisted_targon_model.targon_name} does not support text input")
            if not "embedding" in targon_model["output_modalities"]:
                logger.fatal(f"Whitelisted Targon embedding model {whitelisted_targon_model.targon_name} does not support embedding output")

            if not "EMBEDDING" in targon_model["supported_endpoints"]:
                logger.fatal(f"Whitelisted Targon embedding model {whitelisted_targon_model.targon_name} does not support embedding endpoints")

            max_input_tokens = targon_model["context_length"]
            cost_usd_per_million_input_tokens = float(targon_model["pricing"]["prompt"]) * 1_000_000

            self.embedding_models.append(EmbeddingModelInfo(
                name=whitelisted_targon_model.name,
                external_name=whitelisted_targon_model.targon_name,
                max_input_tokens=max_input_tokens,
                cost_usd_per_million_input_tokens=cost_usd_per_million_input_tokens
            ))

            logger.info(f"Found whitelisted Targon embedding model {whitelisted_targon_model.name}:")
            logger.info(f"  Max input tokens: {max_input_tokens}")
            logger.info(f"  Input cost (USD per million tokens): {cost_usd_per_million_input_tokens}")



        self.targon_client = AsyncOpenAI(
            base_url=config.TARGON_BASE_URL,
            api_key=config.TARGON_API_KEY
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
            chat_completion = await self.targon_client.chat.completions.create(
                model=model_info.external_name,
                temperature=temperature,
                messages=[inference_message_to_openai_message(message) for message in messages],
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
            # Targon returned 4xx or 5xx
            return InferenceResult(
                status_code=e.status_code,
                error_message=e.response.text
            )

        except Exception as e:
            return InferenceResult(
                status_code=-1,
                error_message=f"Error in TargonProvider._inference(): {type(e).__name__}: {str(e)}"
            )



    async def _embedding(
        self,
        *,
        model_info: EmbeddingModelInfo,
        input: str
    ) -> EmbeddingResult:
        try:
            start_time = time()
            create_embedding_response = await self.targon_client.embeddings.create(
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
            # Targon returned 4xx or 5xx
            return EmbeddingResult(
                status_code=e.status_code,
                error_message=e.response.text
            )

        except Exception as e:
            return EmbeddingResult(
                status_code=-1,
                error_message=f"Error in TargonProvider._embedding(): {type(e).__name__}: {str(e)}"
            )