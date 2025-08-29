"""
Base inference provider interface that all providers must implement.
"""


from abc import ABC, abstractmethod
from typing import List, Union, Dict, Any, Optional
from uuid import UUID

from proxy.models import GPTMessage


class InferenceProvider(ABC):
    """Base interface for all inference providers"""
    
    @abstractmethod
    async def inference(
        self,
        run_id: Optional[UUID] = None,
        messages: Optional[List[GPTMessage]] = None,
        temperature: Optional[float] = None,
        model: Optional[str] = None,
    ) -> tuple[str, int]:
        """
        Perform inference with the given parameters.
        
        Args:
            run_id: Evaluation run ID (can be None for dev mode)
            messages: List of messages for the conversation
            temperature: Temperature for inference
            model: Model name to use
            
        Returns:
            tuple[str, int]: (response_text, status_code)
            
        Raises:
            RuntimeError: If the provider is not available or configured
            Exception: For any provider-specific errors
        """
        pass
    
    @abstractmethod
    def get_pricing(self, model: str) -> float:
        """
        Get the pricing per million tokens for the given model.
        
        Args:
            model: Model name
            
        Returns:
            float: Price per million tokens
            
        Raises:
            KeyError: If model is not supported by this provider
        """
        pass
    
    @abstractmethod
    def supports_model(self, model: str) -> bool:
        """
        Check if this provider supports the given model.
        
        Args:
            model: Model name to check
            
        Returns:
            bool: True if model is supported, False otherwise
        """
        pass
    
    @abstractmethod
    def is_available(self) -> bool:
        """
        Check if this provider is available (API key set, dependencies installed, etc).
        
        Returns:
            bool: True if provider is available, False otherwise
        """
        pass
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Get the provider name for logging"""
        pass 