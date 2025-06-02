"""
Base classes for the challenge system hierarchy.

This module defines the abstract base classes that all challenge types
and responses should inherit from.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Any, Optional, List
from datetime import datetime
from enum import Enum

import httpx
from fiber import Keypair


class ChallengeType(Enum):
    """Enumeration of available challenge types."""
    CODEGEN = "codegen"
    REGRESSION = "regression"


@dataclass
class BaseChallenge(ABC):
    """
    Abstract base class for all challenge types.
    
    Contains common fields and methods that all challenges should have.
    """
    challenge_id: str
    problem_statement: str
    commit_hash: Optional[str]
    
    @property
    @abstractmethod
    def challenge_type(self) -> ChallengeType:
        """Return the specific challenge type."""
        pass
    
    @abstractmethod
    def to_dict(self) -> Dict[str, Any]:
        """Convert challenge to dictionary for sending to miners."""
        pass
    
    @abstractmethod
    def get_context_data(self) -> Dict[str, Any]:
        """Return challenge-specific context data."""
        pass
    
    @abstractmethod
    async def send(
        self,
        server_address: str,
        hotkey: str,
        keypair: Keypair,
        node_id: int,
        barrier: "AsyncBarrier",
        db_manager: Optional["DatabaseManager"] = None,
        client: Optional[httpx.AsyncClient] = None,
        timeout: float = 300.0
    ) -> httpx.Response:
        """Send this challenge to a miner node."""
        pass


@dataclass
class BaseResponse(ABC):
    """
    Abstract base class for all challenge responses.
    
    Contains common fields and methods that all responses should have.
    """
    challenge_id: str
    node_id: Optional[int] = None
    miner_hotkey: Optional[str] = None
    response_id: Optional[int] = None
    received_at: Optional[datetime] = None
    score: Optional[float] = None
    evaluated: bool = False
    evaluated_at: Optional[datetime] = None
    response_patch: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert response to dictionary."""
        return {
            "challenge_id": self.challenge_id,
            "node_id": self.node_id,
            "miner_hotkey": self.miner_hotkey,
            "response_id": self.response_id,
            "received_at": self.received_at.isoformat() if self.received_at else None,
            "score": self.score,
            "evaluated": self.evaluated,
            "evaluated_at": self.evaluated_at.isoformat() if self.evaluated_at else None,
            "response_patch": self.response_patch
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'BaseResponse':
        """Create response instance from dictionary."""
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

    def is_evaluated(self) -> bool:
        """Check if the response has been evaluated."""
        return self.evaluated

    def has_valid_patch(self) -> bool:
        """Check if the response has a valid patch."""
        return self.response_patch is not None and len(self.response_patch.strip()) > 0


class ValidationResult:
    """Result of challenge validation."""
    
    def __init__(self, score: float, error: Optional[str] = None):
        self.score = score
        self.error = error
        
    @property
    def is_valid(self) -> bool:
        """Check if validation was successful."""
        return self.error is None
    
    def __repr__(self) -> str:
        if self.is_valid:
            return f"ValidationResult(score={self.score})"
        else:
            return f"ValidationResult(score={self.score}, error='{self.error}')" 