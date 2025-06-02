"""
Codegen response implementation.

This module defines the CodegenResponse class for responses to codegen challenges.
"""

from dataclasses import dataclass
from typing import Dict, Any, Optional
from datetime import datetime

from ..base import BaseResponse


@dataclass
class CodegenResponse(BaseResponse):
    """
    Response to a codegen challenge.
    
    Contains the miner's response patch and evaluation metadata for codegen challenges.
    """
    
    def validate_response_format(self) -> bool:
        """
        Validate that the response has the expected format for codegen challenges.
        
        Returns:
            True if the response format is valid, False otherwise.
        """
        # Basic validation - ensure we have a patch
        if not self.has_valid_patch():
            return False
        
        # Add any codegen-specific validation here
        # For example, checking if the patch contains code changes
        return True
    
    def get_patch_info(self) -> Dict[str, Any]:
        """
        Get information about the response patch.
        
        Returns:
            Dictionary with patch metadata.
        """
        if not self.response_patch:
            return {"has_patch": False, "patch_size": 0}
        
        return {
            "has_patch": True,
            "patch_size": len(self.response_patch),
            "line_count": len(self.response_patch.splitlines()),
            "is_empty": len(self.response_patch.strip()) == 0
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CodegenResponse':
        """Create CodegenResponse instance from dictionary."""
        received_at = data.get('received_at')
        if received_at and isinstance(received_at, str):
            received_at = datetime.fromisoformat(received_at)
        evaluated_at = data.get('evaluated_at')
        if evaluated_at and isinstance(evaluated_at, str):
            evaluated_at = datetime.fromisoformat(evaluated_at)
        
        return cls(
            challenge_id=data['challenge_id'],
            node_id=data.get('node_id'),
            miner_hotkey=data.get('miner_hotkey'),
            response_id=data.get('response_id'),
            received_at=received_at,
            score=data.get('score'),
            evaluated=data.get('evaluated', False),
            evaluated_at=evaluated_at,
            response_patch=data.get('response_patch')
        ) 