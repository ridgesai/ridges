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
        return f"ChallengeTask(node_id={self.node_id}, type={self.challenge.type}, miner={self.miner_hotkey})"


# Get the absolute path to repos dir
validator_dir = Path(__file__).parents[1]
repos_dir = validator_dir / "repos"

SUPPORTED_CODEGEN_REPOS: Dict[str, Path] = {
    "SWE-bench-repos/mwaskom__seaborn": repos_dir / "mwaskom__seaborn",
    "SWE-bench-repos/swe-bench__humanevalfix-python": repos_dir / "swe-bench__humanevalfix-python",
    "SWE-bench-repos/swe-bench__livecodebench-selfrepair": repos_dir / "swe-bench__livecodebench-selfrepair",
    "SWE-bench-repos/pytest-dev__pytest": repos_dir / "pytest-dev__pytest",
    "SWE-bench-repos/pydicom__pydicom": repos_dir / "pydicom__pydicom",
    "SWE-bench-repos/pvlib__pvlib-python": repos_dir / "pvlib__pvlib-python",
    "SWE-bench-repos/marshmallow-code__marshmallow": repos_dir / "marshmallow-code__marshmallow",
    "SWE-bench-repos/pylint-dev__astroid": repos_dir / "pylint-dev__astroid",
    "SWE-bench-repos/sqlfluff__sqlfluff": repos_dir / "sqlfluff__sqlfluff",
    "SWE-bench-repos/psf__requests": repos_dir / "psf__requests",
    "SWE-bench-repos/pydata__xarray": repos_dir / "pydata__xarray",
    "SWE-bench-repos/pylint-dev__pylint": repos_dir / "pylint-dev__pylint",
    "SWE-bench-repos/Qiskit__qiskit": repos_dir / "Qiskit__qiskit",
    "SWE-bench-repos/sympy__sympy": repos_dir / "sympy__sympy",
    "SWE-bench-repos/astropy__astropy": repos_dir / "astropy__astropy",
    "SWE-bench-repos/matplotlib__matplotlib": repos_dir / "matplotlib__matplotlib",
    "SWE-bench-repos/pallets__flask": repos_dir / "pallets__flask",
    "SWE-bench-repos/sphinx-doc__sphinx": repos_dir / "sphinx-doc__sphinx",
    "SWE-bench-repos/django__django": repos_dir / "django__django",
    "SWE-bench-repos/scikit-learn__scikit-learn": repos_dir / "scikit-learn__scikit-learn",
}