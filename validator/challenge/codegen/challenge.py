"""
Codegen challenge implementation.

This module defines the CodegenChallenge class for code generation challenges.
"""

from dataclasses import dataclass
from typing import Dict, Any, List, Optional
from textwrap import dedent

from ..base import BaseChallenge, ChallengeType


@dataclass
class CodegenChallenge(BaseChallenge):
    """
    Code generation challenge.
    
    This challenge type asks miners to generate code based on a problem statement
    and dynamic checklist, using provided context files as reference.
    """
    dynamic_checklist: List[str]
    repository_name: str
    repository_url: str
    context_file_paths: List[str]  # Relative to repository_name as the repo root
    
    # Legacy fields for backward compatibility (should be removed eventually)
    prompt: str = ""
    model: str = ""
    
    @property
    def challenge_type(self) -> ChallengeType:
        """Return the codegen challenge type."""
        return ChallengeType.CODEGEN
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert challenge to dictionary for sending to miners."""
        return {
            "challenge_id": self.challenge_id,
            "problem_statement": self.problem_statement,
            "dynamic_checklist": self.dynamic_checklist,
            "repository_name": self.repository_name,
            "repository_url": self.repository_url,
            "commit_hash": self.commit_hash,
            "context_file_paths": self.context_file_paths
        }
    
    def get_context_data(self) -> Dict[str, Any]:
        """Return codegen-specific context data."""
        return {
            "dynamic_checklist": self.dynamic_checklist,
            "repository_name": self.repository_name,
            "repository_url": self.repository_url,
            "context_file_paths": self.context_file_paths
        }
    
    def to_detailed_format(self) -> str:
        """
        Format challenge for detailed display.
        
        Returns:
            Formatted string with problem statement, checklist, and context files.
        """
        context_files_string = ""
        for i, file in enumerate(self.context_file_paths):
            context_files_string += f"# File {i} used to solve the problem: {file}\n"
        
        return dedent(f"""
        Problem Statement: {self.problem_statement}
        Checklist of items to consider: {self.dynamic_checklist}
        {context_files_string}
        """).strip()
    
    def get_repository_info(self) -> Dict[str, str]:
        """Get repository-related information."""
        return {
            "repository_name": self.repository_name,
            "repository_url": self.repository_url,
            "commit_hash": self.commit_hash or "latest"
        }
    
    def has_context_files(self) -> bool:
        """Check if the challenge has context files."""
        return bool(self.context_file_paths)
    
    def context_file_count(self) -> int:
        """Get the number of context files."""
        return len(self.context_file_paths) 