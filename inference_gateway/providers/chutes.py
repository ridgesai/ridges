import utils.logger as logger
import inference_gateway.config as config

from typing import List
from http import HTTPStatus
from openai import AsyncOpenAI, APIStatusError
from inference_gateway.models import ModelInfo, InferenceResult, InferenceMessage



chutes_client = AsyncOpenAI(
    base_url=config.CHUTES_BASE_URL,
    api_key=config.CHUTES_API_KEY
)



SUPPORTED_MODEL_INFO = [
    ModelInfo(
        name="Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8",
        cost_usd_per_million_input_tokens=0.22,
        cost_usd_per_million_output_tokens=0.95
    ),
    ModelInfo(
        name="zai-org/GLM-4.5-FP8",
        cost_usd_per_million_input_tokens=0.35,
        cost_usd_per_million_output_tokens=1.55
    ),
    ModelInfo(
        name="deepseek-ai/DeepSeek-V3-0324",
        cost_usd_per_million_input_tokens=0.24,
        cost_usd_per_million_output_tokens=0.84
    ),
    ModelInfo(
        name="moonshotai/Kimi-K2-Instruct", # moonshotai/Kimi-K2-Instruct-0905
        cost_usd_per_million_input_tokens=0.39,
        cost_usd_per_million_output_tokens=1.9
    ),
    ModelInfo(
        name="zai-org/GLM-4.6-FP8",
        cost_usd_per_million_input_tokens=0.5,
        cost_usd_per_million_output_tokens=1.75
    )
]



def is_model_supported_for_inference(model: str) -> bool:
    return model in [model.name for model in SUPPORTED_MODEL_INFO]

async def inference(model: str, temperature: float, messages: List[InferenceMessage]) -> InferenceResult:
    NUM_CHARS_TO_LOG = 30

    try:
        model_info = next((model_info for model_info in SUPPORTED_MODEL_INFO if model_info.name == model), None)

        request_first_chars = messages[-1].content.replace('\n', '')[:NUM_CHARS_TO_LOG] if messages else ''
        logger.info(f"--> Inference request for model {model} with {sum(len(message.content) for message in messages)} characters; first {NUM_CHARS_TO_LOG} chars of last message: '{request_first_chars}'")

        try:
            chat_completion = await chutes_client.chat.completions.create(
                messages=messages,
                model=model,
                temperature=temperature
            )
        except APIStatusError as e:
            # Chutes returned 4xx or 5xx
            logger.info(f"<-- Inference response for model {model}: {e.status_code} {HTTPStatus(e.status_code).phrase}: {e.response.text}")

            return InferenceResult(
                status_code=e.status_code,
                response=e.response.text
            )
        
        response = chat_completion.choices[0].message.content

        num_input_tokens = chat_completion.usage.prompt_tokens
        num_output_tokens = chat_completion.usage.completion_tokens
        cost_usd = model_info.get_cost_usd(num_input_tokens, num_output_tokens) if model_info else None

        response_first_chars = response.replace('\n', '')[:NUM_CHARS_TO_LOG]
        logger.info(f"<-- Inference response for model {model} with {len(response)} characters; first {NUM_CHARS_TO_LOG} chars: '{response_first_chars}'")

        return InferenceResult(
            status_code=200,
            response=response,
            num_input_tokens=num_input_tokens,
            num_output_tokens=num_output_tokens,
            cost_usd=cost_usd
        )

    except Exception as e:
        logger.info(f"<-- Inference response for model {model}: Error in chutes.inference() {e.response.text}")
        return InferenceResult(
            status_code=-1,
            response=f"Error in chutes.inference(): {type(e).__name__}: {str(e)}"
        )



def is_model_supported_for_embedding(model: str) -> bool:
    # TODO
    return False

async def embedding(model: str, input: str) -> List[float]:
    # TODO
    pass



async def test_all_models():
    for model in SUPPORTED_MODEL_INFO:
        logger.info(f"Testing {model.name}...")
        response = await inference(model=model.name, temperature=0.5, messages=[InferenceMessage(role="user", content="What is 2+2?")])