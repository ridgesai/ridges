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
            logger.warning(f"Unsupported model requested for run {run_id}: {model}")
            return {"error": str(e)}
        
        # Convert messages to dict format for database storage
        messages_dict = []
        if messages:
            for message in messages:
                if message:
                    messages_dict.append({"role": message.role, "content": message.content})

        # Create inference record in database (skip in dev mode)
        inference_id = None
        if ENV != 'dev':
            inference_id = await create_inference(run_id, messages_dict, temperature, model, primary_provider.name)

        # Try primary provider first
        primary_error = None
        try:
            logger.debug(f"Trying {primary_provider.name} for model {model}")
            response_text = await primary_provider.inference(run_id, messages, temperature, model)
            
            # Calculate cost using provider-specific pricing
            total_tokens = len(response_text) // 4  # Rough estimate: 1 token ≈ 4 chars
            cost = (total_tokens / 1_000_000) * primary_provider.get_pricing(model)
            
            # Update inference record with cost and response (skip in dev mode)
            if ENV != 'dev' and inference_id:
                await update_inference(inference_id, cost, response_text, total_tokens)
            
            logger.debug(f"{primary_provider.name} inference for run {run_id} completed, tokens: {total_tokens}, cost: ${cost:.6f}")
            return response_text
            
        except Exception as e:
            primary_error = str(e)
            logger.error(f"{primary_provider.name} inference failed for run {run_id}: {e}")
        
        # Try fallback provider if available
        fallback_provider = self._get_fallback_provider(model, primary_provider)
        if fallback_provider:
            logger.info(f"Attempting {fallback_provider.name} fallback for model {model} after {primary_provider.name} failure")
            try:
                response_text = await fallback_provider.inference(run_id, messages, temperature, model)
                
                # Calculate cost using fallback provider pricing
                total_tokens = len(response_text) // 4
                cost = (total_tokens / 1_000_000) * fallback_provider.get_pricing(model)
                
                # Update inference record with cost and response (skip in dev mode)
                if ENV != 'dev' and inference_id:
                    await update_inference(inference_id, cost, response_text, total_tokens, fallback_provider.name)
                
                logger.info(f"{fallback_provider.name} fallback successful for run {run_id}, tokens: {total_tokens}, cost: ${cost:.6f}")
                return response_text
                
            except Exception as fallback_error:
                logger.error(f"{fallback_provider.name} fallback also failed for run {run_id}: {fallback_error}")
                # Update inference record with both errors (skip in dev mode)
                if ENV != 'dev' and inference_id:
                    await update_inference(
                        inference_id, 
                        0.0, 
                        f"{primary_provider.name} error: {primary_error} | {fallback_provider.name} fallback error: {str(fallback_error)}", 
                        0,
                        None  # Keep original provider since both failed
                    )
                return {"error": f"{primary_provider.name} error: {primary_error} | {fallback_provider.name} fallback also failed: {str(fallback_error)}"}
        
        # No fallback available or model doesn't support fallback
        if ENV != 'dev' and inference_id:
            await update_inference(inference_id, 0.0, primary_error, 0, None)
        return {"error": f"Error in inference request: {primary_error}"} 