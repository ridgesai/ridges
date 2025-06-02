"""
Common utility classes for the challenge system.

This module contains utility classes and helpers that are used across
different challenge types but don't belong to any specific challenge type.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Union
from pydantic import BaseModel

from .base import BaseChallenge


@dataclass 
class IngestionHeuristics:
    """
    Helper that lets the validator set the scope of files they want to select for a challenge.
    """
    min_files_to_consider_dir_for_problems: int
    min_file_content_len: int


@dataclass
class File:
    """
    Helper class to handle and later embed a file for which we will ask a problem statement.
    """
    path: Path
    contents: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": str(self.path),
            "contents": self.contents
        }


@dataclass
class EmbeddedFile:
    """
    File with embedding information for similarity search.
    """
    path: str
    contents: str
    embedding: List[float]

    def __str__(self):
        return f"File: {self.path}, Length: {len(self.contents)}"

    def __repr__(self) -> str:
        return f"EmbeddedFile(path='{self.path}', content_length={len(self.contents)})"
 

@dataclass
class FilePair:
    """
    Pair of files with their cosine similarity score.
    """
    cosine_similarity: float
    files: List[EmbeddedFile]


@dataclass 
class CodegenProblemLLMResponse(BaseModel):
    """
    Response format expected from LLM when generating codegen problems.
    """
    problem_statement: str
    dynamic_checklist: List[str]


class ChallengeTask:
    """
    Represents an active challenge task being executed.
    """
    def __init__(
        self, 
        node_id: int, 
        task: asyncio.Task, 
        timestamp: datetime, 
        challenge: BaseChallenge, 
        miner_hotkey: str
    ):
        self.node_id = node_id
        self.task = task
        self.timestamp = timestamp
        self.challenge = challenge
        self.miner_hotkey = miner_hotkey
    
    def __repr__(self) -> str:
        return f"ChallengeTask(node_id={self.node_id}, challenge_type={self.challenge.challenge_type.value}, miner={self.miner_hotkey})"


# Get the absolute path to repos dir
validator_dir = Path(__file__).parents[1]
repos_dir = validator_dir / "repos"

SUPPORTED_CODEGEN_REPOS: Dict[str, Path] = {
    "mwaskom/seaborn": repos_dir / "seaborn",
    "pytest-dev/pytest":  repos_dir / "pytest",
} 