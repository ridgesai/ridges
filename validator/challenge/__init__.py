"""
Challenge system for the validator.

This package provides a hierarchical structure for different types of challenges
and their corresponding responses.
"""

# Base classes
from .base import BaseChallenge, BaseResponse, ChallengeType, ValidationResult

# Codegen challenges
from .codegen import CodegenChallenge, CodegenResponse

# Regression challenges  
from .regression import RegressionChallenge, RegressionResponse

# Common utilities
from .common import (
    IngestionHeuristics,
    File,
    EmbeddedFile,
    FilePair,
    CodegenProblemLLMResponse,
    ChallengeTask,
    SUPPORTED_CODEGEN_REPOS
)

__all__ = [
    # Base classes
    "BaseChallenge",
    "BaseResponse", 
    "ChallengeType",
    "ValidationResult",
    
    # Codegen
    "CodegenChallenge",
    "CodegenResponse",
    
    # Regression
    "RegressionChallenge", 
    "RegressionResponse",
    
    # Common utilities
    "IngestionHeuristics",
    "File",
    "EmbeddedFile",
    "FilePair", 
    "CodegenProblemLLMResponse",
    "ChallengeTask",
    "SUPPORTED_CODEGEN_REPOS",
]
