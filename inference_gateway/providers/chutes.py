import utils.logger as logger
import inference_gateway.config as config

from typing import List
from openai import AsyncOpenAI
from inference_gateway.models import InferenceMessage



chutes_client = AsyncOpenAI(
    base_url=config.CHUTES_BASE_URL,
    api_key=config.CHUTES_API_KEY
)



async def inference(model: str, temperature: float, messages: List[InferenceMessage]) -> str:
    NUM_CHARS_TO_LOG = 30

    request_first_chars = messages[-1].content.replace('\n', '')[:NUM_CHARS_TO_LOG] if messages else ''
    logger.info(f"--> Inference request for model {model} (temperature {temperature}) with {sum(len(message.content) for message in messages)} characters; first {NUM_CHARS_TO_LOG} chars of last message: '{request_first_chars}'")
    
    response = (await chutes_client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        stream=False
    )).choices[0].message.content

    response_first_chars = response.replace('\n', '')[:NUM_CHARS_TO_LOG]
    logger.info(f"<-- Inference response for model {model} (temperature {temperature}) with {len(response)} characters; first {NUM_CHARS_TO_LOG} chars: '{response_first_chars}'")

    return response