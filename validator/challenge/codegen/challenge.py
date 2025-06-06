"""
Codegen challenge implementation.

This module defines the CodegenChallenge class for code generation challenges.
"""

from dataclasses import dataclass
from typing import Dict, Any, List, Optional
from textwrap import dedent
from datetime import datetime

from shared.logging_utils import get_logger

from validator.db.operations import DatabaseManager

from ..base import BaseChallenge
from .response import CodegenResponse

logger = get_logger(__name__)


@dataclass
class CodegenChallenge(BaseChallenge):
    """
    Code generation challenge.
    
    This challenge type asks miners to generate code based on a problem statement
    and dynamic checklist, using provided context files as reference.
    """
    problem_statement: str
    dynamic_checklist: List[str]
    repository_url: str
    context_file_paths: List[str]  # Relative to repository_url as the repo root
    
    prompt: str = ""
    model: str = ""
    
    @property
    def type(self) -> str:
        """Get the type of challenge"""
        return "codegen"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert challenge to dictionary for sending to miners."""
        return {
            "challenge_id": self.challenge_id,
            "problem_statement": self.problem_statement,
            "dynamic_checklist": self.dynamic_checklist,
            "repository_url": self.repository_url,
            "commit_hash": self.commit_hash,
            "context_file_paths": self.context_file_paths,
            "validator_hotkey": self.validator_hotkey
        }
    
    def to_database_dict(self) -> Dict[str, Any]:
        """Convert challenge to dictionary for database storage."""
        return {
            "problem_statement": self.problem_statement,
            "dynamic_checklist": self.dynamic_checklist,
            "repository_url": self.repository_url,
            "commit_hash": self.commit_hash,
            "context_file_paths": self.context_file_paths
        }
    
    @classmethod
    def from_database_dict(cls, data: Dict[str, Any]) -> "CodegenChallenge":
        """Create CodegenChallenge from database data."""
        return cls(
            challenge_id=data["challenge_id"],
            problem_statement=data["problem_statement"],
            dynamic_checklist=data["dynamic_checklist"],
            repository_url=data["repository_url"],
            commit_hash=data["commit_hash"],
            context_file_paths=data["context_file_paths"],
            validator_hotkey=data["validator_hotkey"]
        )
    
    def store_in_database(self, db_manager: DatabaseManager) -> None:
        """Store this challenge in the database."""
        db_manager.store_challenge(
            challenge_id=self.challenge_id,
            type="codegen",
            challenge_data=self.to_database_dict(),
            validator_hotkey=self.validator_hotkey
        )
    
    @classmethod
    def get_from_database(cls, db_manager, challenge_id: str) -> Optional["CodegenChallenge"]:
        """Get challenge from database."""
        data = db_manager.get_challenge_data(challenge_id, "codegen")
        if data is None:
            return None
        return cls.from_database_dict(data)
    
    def get_context_data(self) -> Dict[str, Any]:
        """Return codegen-specific context data."""
        return {
            "dynamic_checklist": self.dynamic_checklist,
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
            "repository_url": self.repository_url,
            "commit_hash": self.commit_hash or "latest"
        }
    
    def has_context_files(self) -> bool:
        """Check if the challenge has context files."""
        return bool(self.context_file_paths)
    
    def context_file_count(self) -> int:
        """Get the number of context files."""
        return len(self.context_file_paths)
    
    def create_response_object(self, challenge_id: str, hotkey: str, node_id: int, 
                             received_at: datetime, response_patch: Optional[str]):
        """
        Create a CodegenResponse object for this challenge type.
        
        Args:
            challenge_id: The challenge ID
            hotkey: Miner's hotkey
            node_id: Miner's node ID
            received_at: When the response was received
            response_patch: The response patch content
            
        Returns:
            CodegenResponse object
        """
        return CodegenResponse(
            challenge_id=challenge_id,
            miner_hotkey=hotkey,
            node_id=node_id,
            received_at=received_at,
            response_patch=response_patch
        )