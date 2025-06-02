"""
Regression challenge implementation.

This module defines the RegressionChallenge class for regression testing challenges.
"""

from dataclasses import dataclass
from typing import Dict, Any, List, Optional

from ..base import BaseChallenge, ChallengeType


@dataclass
class RegressionChallenge(BaseChallenge):
    """
    Regression testing challenge.
    
    This challenge type simulates a pull request that introduces a bug,
    asking miners to identify and fix issues that cause previously-passing tests to fail.
    """
    repository_url: str
    context_file_paths: List[str]
    
    @property
    def challenge_type(self) -> ChallengeType:
        """Return the regression challenge type."""
        return ChallengeType.REGRESSION
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert challenge to dictionary for sending to miners."""
        return {
            "challenge_id": self.challenge_id,
            "repository_url": self.repository_url,
            "commit_hash": self.commit_hash,
            "problem_statement": self.problem_statement,
            "context_file_paths": self.context_file_paths,
        }
    
    def get_context_data(self) -> Dict[str, Any]:
        """Return regression-specific context data."""
        return {
            "repository_url": self.repository_url,
            "context_file_paths": self.context_file_paths
        }
    
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
    
    def is_public_repository(self) -> bool:
        """Check if the repository URL appears to be public."""
        # Simple heuristic - check for common public repo patterns
        public_patterns = [
            "github.com",
            "gitlab.com", 
            "bitbucket.org"
        ]
        return any(pattern in self.repository_url.lower() for pattern in public_patterns) 