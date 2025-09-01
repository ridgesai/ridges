"""
Targon provider for inference requests (fallback provider).
"""

import logging
import random
from typing import List
from uuid import UUID

try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

from .base import InferenceProvider
from proxy.models import GPTMessage
from proxy.config import (
    TARGON_API_KEY,
    TARGON_FALLBACK_MODELS,
    TARGON_PRICING,
    MODEL_REDIRECTS,
)

logger = logging.getLogger(__name__)


class TargonProvider(InferenceProvider):
    """Provider for Targon API inference (fallback provider)"""
    
    def __init__(self):
        self.api_key = TARGON_API_KEY
        
    @property
    def name(self) -> str:
        return "Targon"
    
    def is_available(self) -> bool:
        """Check if Targon provider is available"""
        return bool(self.api_key) and OPENAI_AVAILABLE
    
    def supports_model(self, model: str) -> bool:
        """Check if model is supported by Targon"""
        return model in TARGON_FALLBACK_MODELS
    
    def get_pricing(self, model: str) -> float:
        """Get Targon pricing for the model"""
        if not self.supports_model(model):
            raise KeyError(f"Model {model} not supported by Targon provider")
        return TARGON_PRICING[model]
    
    async def inference(
        self,
        run_id: UUID = None,
        messages: List[GPTMessage] = None,
        temperature: float = None,
        model: str = None,
    ) -> tuple[str, int]:
        """Perform inference using Targon API"""
        
        if not self.is_available():
            if not self.api_key:
                raise RuntimeError("Targon API key not set")
            if not OPENAI_AVAILABLE:
                raise RuntimeError("OpenAI package not available for Targon client")
            
        if not self.supports_model(model):
            raise ValueError(f"Model {model} not supported by Targon provider")
        
        # Apply model redirects (e.g., GLM-4.5-FP8 -> GLM-4.5)
        actual_model = MODEL_REDIRECTS.get(model, model)
        
        try:
            client = openai.OpenAI(
                base_url="https://api.targon.com/v1",
                api_key=self.api_key
            )
            
            # Convert messages to OpenAI format
            openai_messages = []
            for msg in messages:
                openai_messages.append({"role": msg.role, "content": msg.content})
            
            # logger.debug(f"Targon inference request for run {run_id} with model {model}")
            
            response = client.chat.completions.create(
                model=actual_model,
                stream=True,
                messages=openai_messages,
                temperature=temperature,
                max_tokens=2048,
                top_p=1,
                frequency_penalty=0,
                presence_penalty=0,
                seed=random.randint(0, 2**32 - 1),
            )
            
            # Handle streaming response
            response_text = ""
            for chunk in response:
                if chunk.choices and len(chunk.choices) > 0:
                    delta = chunk.choices[0].delta
                    if hasattr(delta, 'content') and delta.content is not None:
                        response_text += delta.content
            
            #logger.debug(f"Targon inference for run {run_id} completed")
            
            # Validate that we received actual content
            if not response_text.strip():
                error_msg = f"Targon API returned empty response for model {model}. This may indicate API issues or streaming problems."
                # logger.error(f"Empty response for run {run_id}: {error_msg}")
                return error_msg, 200  # Status was 200 but response was empty
            
            return response_text, 200
            
        except Exception as e:
            logger.error(f"Targon inference error for run {run_id}: {e}")
            # Extract status code from OpenAI errors if possible
            status_code = getattr(e, 'status_code', None) or 500
            return str(e), status_code 