"""
Chutes provider for inference requests.
"""

import json
import logging
import random
import httpx
from typing import List
from uuid import UUID

import httpx

from .base import InferenceProvider
from proxy.models import GPTMessage
from proxy.config import (
    CHUTES_API_KEY,
    CHUTES_INFERENCE_URL,
)

logger = logging.getLogger(__name__)

CHUTES_ALLOWED_MODELS = [
    "deepseek-ai/DeepSeek-V3-0324",
    "agentica-org/DeepCoder-14B-Preview",
    "deepseek-ai/DeepSeek-V3",
    "deepseek-ai/DeepSeek-R1",
    "deepseek-ai/DeepSeek-R1-0528",
    "NousResearch/DeepHermes-3-Mistral-24B-Preview",
    "NousResearch/DeepHermes-3-Llama-3-8B-Preview",
    "chutesai/Llama-4-Maverick-17B-128E-Instruct-FP8",
    "Qwen/Qwen3-32B",
    "Qwen/Qwen3-235B-A22B-Instruct-2507",
    "Qwen/QwQ-32B",
    "chutesai/Mistral-Small-3.2-24B-Instruct-2506",
    "unsloth/gemma-3-27b-it",
    "agentica-org/DeepCoder-14B-Preview",
    "THUDM/GLM-Z1-32B-0414",
    "ArliAI/QwQ-32B-ArliAI-RpR-v1",
    "Qwen/Qwen3-30B-A3B",
    "hutesai/Devstral-Small-2505",
    "chutesai/Mistral-Small-3.1-24B-Instruct-2503",
    "chutesai/Llama-4-Scout-17B-16E-Instruct",
    "shisa-ai/shisa-v2-llama3.3-70b",
    "moonshotai/Kimi-Dev-72B",
    "moonshotai/Kimi-K2-Instruct",
    "all-hands/openhands-lm-32b-v0.1",
    "zai-org/GLM-4.5-FP8",
    "zai-org/GLM-4.5-Air",
]

class ChutesProvider(InferenceProvider):
    """Provider for Chutes API inference"""
    model_pricing = {}
    
    def __init__(self):
        self.api_key = CHUTES_API_KEY
    
    @staticmethod
    async def load_model_pricing():
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get("https://llm.chutes.ai/v1/models")
                response.raise_for_status()

                for model in response.json().get("data", []):
                    model_id = model.get("id")
                    price_usd = model.get("pricing", {}).get("completion")
                    if model_id and price_usd is not None:
                        ChutesProvider.model_pricing[model_id] = price_usd
        except Exception as e:
            logger.error(f"Error loading model pricing: {e}")
        
    @property
    def name(self) -> str:
        return "Chutes"
    
    def is_available(self) -> bool:
        """Check if Chutes provider is available"""
        return bool(self.api_key)

    def supports_model(self, model: str) -> bool:
        """Check if model is supported by Chutes (supports all models in pricing)"""
        return model in ChutesProvider.model_pricing
    
    def allowed(self, model: str) -> bool:
        return model in CHUTES_ALLOWED_MODELS
    
    def get_pricing(self, model: str) -> float:
        """Get Chutes pricing for the model"""
        if not self.supports_model(model):
            raise KeyError(f"Model {model} not supported by Chutes provider")
        return ChutesProvider.model_pricing[model]
    
    async def inference(
        self,
        run_id: UUID = None,
        messages: List[GPTMessage] = None,
        temperature: float = None,
        model: str = None,
    ) -> tuple[str, int]:
        """Perform inference using Chutes API"""
        
        if not self.is_available():
            raise RuntimeError("Chutes API key not set")
            
        if not self.supports_model(model):
            raise ValueError(f"Model {model} not supported by Chutes provider")
        
        if not self.allowed(model):
            raise ValueError(f"Model {model} not allowed")
        
        # Convert messages to dict format
        messages_dict = []
        if messages:
            for message in messages:
                if message:
                    messages_dict.append({"role": message.role, "content": message.content})

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        body = {
            "model": model,
            "messages": messages_dict,
            "stream": True,
            "max_tokens": 2048,
            "temperature": temperature,
            "seed": random.randint(0, 2**32 - 1),
        }

        logger.debug(f"Chutes inference request for run {run_id} with model {model}")

        response_text = ""
        
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", CHUTES_INFERENCE_URL, headers=headers, json=body) as response:

                if response.status_code != 200:
                    error_text = await response.aread()
                    if isinstance(error_text, bytes):
                        error_message = error_text.decode()
                    else:
                        error_message = str(error_text)
                    logger.error(
                        f"Chutes API request failed for run {run_id}: {response.status_code} - {error_message}"
                    )
                    return error_message, response.status_code

                # Process streaming response
                async for chunk in response.aiter_lines():
                    if chunk:
                        chunk_str = chunk.strip()
                        if chunk_str.startswith("data: "):
                            chunk_data = chunk_str[6:]  # Remove "data: " prefix

                            if chunk_data == "[DONE]":
                                break

                            try:
                                chunk_json = json.loads(chunk_data)
                                if "choices" in chunk_json and len(chunk_json["choices"]) > 0:
                                    choice = chunk_json["choices"][0]
                                    if "delta" in choice and "content" in choice["delta"]:
                                        content = choice["delta"]["content"]
                                        if content:
                                            response_text += content

                            except json.JSONDecodeError:
                                # Skip malformed JSON chunks
                                continue

        logger.debug(f"Chutes inference for run {run_id} completed")
        
        # Validate that we received actual content
        if not response_text.strip():
            error_msg = f"Chutes API returned empty response for model {model}. This may indicate API issues or malformed streaming response."
            logger.error(f"Empty response for run {run_id}: {error_msg}")
            return error_msg, 200  # Status was 200 but response was empty
        
        return response_text, 200 