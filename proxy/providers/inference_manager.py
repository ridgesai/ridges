"""
Inference manager that orchestrates all providers and handles fallback logic.
"""

import logging
from typing import List, Union, Dict, Any
from uuid import UUID

from .base import InferenceProvider
from .chutes_provider import ChutesProvider
from .targon_provider import TargonProvider

from proxy.models import GPTMessage
from proxy.config import ENV, MODEL_PRICING
from proxy.database import create_inference, update_inference

logger = logging.getLogger(__name__)


class InferenceManager:
    """Manages inference requests across multiple providers with fallback logic"""
    
    def __init__(self):
        # Initialize all providers
        self.chutes = ChutesProvider()
        self.targon = TargonProvider()
        
        # Define provider priority order (specific providers first, then general ones)
        self.providers = [
            self.chutes,    # General provider for most models (primary)
            self.targon,    # Fallback provider for specific models
        ]
    
    def _find_provider(self, model: str) -> InferenceProvider:
        """Find the primary provider for the given model"""
        # Prioritize Targon for models it supports (avoid unnecessary Chutes attempts)
        # if self.targon.supports_model(model) and self.targon.is_available():
        #     return self.targon
            
        for provider in self.providers:
            if provider.supports_model(model) and provider.is_available():
                return provider
        
        # If no provider found, raise error with available options
        available_models = []
        for provider in self.providers:
            if provider.is_available():
                for model_name in MODEL_PRICING.keys():
                    if provider.supports_model(model_name):
                        available_models.append(model_name)
        
        raise ValueError(f"Model {model} not supported. Available models: {sorted(set(available_models))}")
    
    def _get_fallback_provider(self, model: str, failed_provider: InferenceProvider) -> InferenceProvider:
        """Get fallback provider for the given model (if any)"""
        # Only Targon is currently set up as a fallback provider
        if failed_provider != self.targon and self.targon.supports_model(model) and self.targon.is_available():
            return self.targon
        return None
    
    async def inference(
        self,
        run_id: UUID = None,
        messages: List[GPTMessage] = None,
        temperature: float = None,
        model: str = None,
    ) -> Union[str, Dict[str, Any]]:
        """
        Perform inference with automatic provider selection and fallback.
        
        Returns:
            str: Successful inference response
            Dict[str, Any]: Error response with {"error": "message"}
        """
        
        # Validate model is supported
        try:
            primary_provider = self._find_provider(model)
        except ValueError as e:
            # logger.warning(f"Unsupported model requested for run {run_id}: {model}")
            return {"error": str(e)}
        
        # Convert messages to dict format for database storage
        messages_dict = []
        if messages:
            for message in messages:
                if message:
                    messages_dict.append({"role": message.role, "content": message.content})

        # Create inference record in database before starting (skip in dev mode)
        inference_id = None
        if ENV != 'dev':
            inference_id = await create_inference(run_id, messages_dict, temperature, model, primary_provider.name)

        # Try primary provider first
        # logger.debug(f"Trying {primary_provider.name} for model {model}")
        response_text, status_code = await primary_provider.inference(run_id, messages, temperature, model)
        
        # Calculate cost and tokens
        if status_code == 200:
            total_tokens = len(response_text) // 4  # Rough estimate: 1 token ≈ 4 chars
            cost = (total_tokens / 1_000_000) * primary_provider.get_pricing(model)
        else:
            total_tokens = 0
            cost = 0.0
        
        # Update inference record with results (skip in dev mode)
        if ENV != 'dev' and inference_id:
            await update_inference(inference_id, cost, response_text, total_tokens, primary_provider.name, status_code)
        
        # If primary succeeded, return
        if status_code == 200:
            logger.debug(f"{primary_provider.name} inference for run {run_id} completed, tokens: {total_tokens}, cost: ${cost:.6f}")
            return response_text
        
        # Primary failed, log the error
        logger.error(f"{primary_provider.name} inference failed for run {run_id}: status {status_code}, response: {response_text}")
        
        # Try fallback provider if available
        fallback_provider = self._get_fallback_provider(model, primary_provider)
        if fallback_provider:
            # logger.info(f"Attempting {fallback_provider.name} fallback for model {model} after {primary_provider.name} failure")
            
            # Create separate inference record for fallback attempt (skip in dev mode)
            fallback_inference_id = None
            if ENV != 'dev':
                fallback_inference_id = await create_inference(run_id, messages_dict, temperature, model, fallback_provider.name)
            
            fallback_response, fallback_status = await fallback_provider.inference(run_id, messages, temperature, model)
            
            # Calculate cost and tokens for fallback
            if fallback_status == 200:
                fallback_tokens = len(fallback_response) // 4
                fallback_cost = (fallback_tokens / 1_000_000) * fallback_provider.get_pricing(model)
            else:
                fallback_tokens = 0
                fallback_cost = 0.0
            
            # Update fallback inference record with results (skip in dev mode)
            if ENV != 'dev' and fallback_inference_id:
                await update_inference(fallback_inference_id, fallback_cost, fallback_response, fallback_tokens, fallback_provider.name, fallback_status)
            
            if fallback_status == 200:
                logger.info(f"{fallback_provider.name} fallback successful for run {run_id}, tokens: {fallback_tokens}, cost: ${fallback_cost:.6f}")
                return fallback_response
            else:
                logger.error(f"{fallback_provider.name} fallback also failed for run {run_id}: status {fallback_status}, response: {fallback_response}")
                return {"error": f"{primary_provider.name} error: {response_text} | {fallback_provider.name} fallback also failed: {fallback_response}"}
        
        # No fallback available or model doesn't support fallback
        return {"error": f"Error in inference request: {response_text}"} 