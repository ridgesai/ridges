import utils.logger as logger
import inference_gateway.config as config

from typing import List
from openai import OpenAI
from inference_gateway.models import InferenceMessage



chutes_client = OpenAI(
    base_url=config.CHUTES_BASE_URL,
    api_key=config.CHUTES_API_KEY
)



def inference(model: str, temperature: float, messages: List[InferenceMessage]) -> str:
    logger.info(f"--> Inference request for model {model} (temperature {temperature}) with {sum(len(message.content) for message in messages)} characters")
    
    response = chutes_client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        stream=False
    ).choices[0].message.content

    logger.info(f"<-- Inference response for model {model} (temperature {temperature}) with {len(response)} characters")

    return response