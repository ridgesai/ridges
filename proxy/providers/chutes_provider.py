"""
Chutes provider for inference requests.
"""

import json
import logging
import random
from typing import List
from uuid import UUID

import httpx

from .base import InferenceProvider
from proxy.models import GPTMessage
from proxy.config import (
    CHUTES_API_KEY,
    CHUTES_INFERENCE_URL,
    MODEL_PRICING,
)

logger = logging.getLogger(__name__)


class ChutesProvider(InferenceProvider):
    """Provider for Chutes API inference"""
    
    def __init__(self):
        self.api_key = CHUTES_API_KEY
        
    @property
    def name(self) -> str:
        return "Chutes"
    
    def is_available(self) -> bool:
        """Check if Chutes provider is available"""
        return bool(self.api_key)
    
    def supports_model(self, model: str) -> bool:
        """Check if model is supported by Chutes (supports all models in pricing)"""
        return model in MODEL_PRICING
    
    def get_pricing(self, model: str) -> float:
        """Get Chutes pricing for the model"""
        if not self.supports_model(model):
            raise KeyError(f"Model {model} not supported by Chutes provider")
        return MODEL_PRICING[model]
    
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

        # logger.debug(f"Chutes inference request for run {run_id} with model {model}")

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
                        f"Chutes API request failed for run {run_id} (model: {model}): {response.status_code} - {error_message}"
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

        # logger.debug(f"Chutes inference for run {run_id} completed")
        
        # Validate that we received actual content
        if not response_text.strip():
            # Don't care too much about empty responses for now
            error_msg = f"Chutes API returned empty response for model {model}. This may indicate API issues or malformed streaming response."
            # logger.error(f"Empty response for run {run_id}: {error_msg}")
            
            return error_msg, 200  # Status was 200 but response was empty
        
        return response_text, 200 