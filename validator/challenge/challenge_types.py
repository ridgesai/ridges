from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from pathlib import Path
from textwrap import dedent
from datetime import datetime

import asyncio

'''
Helper that lets the validator set the scope of files they want to select for a challenge
'''
@dataclass
class IngestionHeuristics:
    min_files_to_consider_dir_for_problems: int
    min_file_content_len: int

'''
Helper class to handle and later embed a file for which we will ask a problem statement
'''
@dataclass
class File:
    path: Path
    contents: str


@dataclass
class EmbeddedFile:
    path: str
    contents: str
    embedding: List

    def __str__(self):
        return f"File: {self.path}, Length: {len(self.contents)}"

    def __repr__(self) -> str:
        return f"File: {self.path}, Length: {len(self.contents)}"
 

@dataclass
class FilePair:
    cosine_similarity: float
    files: List[EmbeddedFile]

@dataclass 
class CodegenProblemLLMResponse:
    problem_statement: str
    dynamic_checklist: List[str]

@dataclass
class HyrdatedGeneratedCodegenProblem:
    challenge_id: str
    prompt: str
    model: str
    problem_statement: str
    dynamic_checklist: List[str]
    repository_name: str
    commit_hash: Optional[str]
    context_files: FilePair

    def to_detailed_format(self) -> str:
        context_files_string = ""
        for i, file in enumerate(self.context_files):
            context_files_string += f"# File {i} used to solve the problem: {file}"
        return dedent(f"""
        Problem Statement: {self.problem_statement}
        Checklist of items to consider: {self.dynamic_checklist}
        {context_files_string}
        """)

    def to_dict(self) -> Dict[str, Any]:
        """Convert challenge to dictionary for sending to miners"""
        return {
            "challenge_id": self.challenge_id,
            "context_files": self.context_files.files,
            "repository_name": self.repository_name,
            "problem_statement": self.problem_statement,
            "dynamic_checklist": self.dynamic_checklist
        }

class ValidationResult:
    def __init__(self, 
        score: float,
        error: Optional[str] = None):
        self.score = score
        self.error = error
        
    @property
    def is_valid(self) -> bool:
        return self.error is None


class ChallengeTask:
    def __init__(self, node_id: int, task: asyncio.Task, timestamp: datetime, challenge: HyrdatedGeneratedCodegenProblem, miner_hotkey: str):
        self.node_id = node_id
        self.task = task
        self.timestamp = timestamp
        self.challenge = challenge
        self.miner_hotkey = miner_hotkey

@dataclass
class CodegenResponse:
    """Expected response format for codegen challenges"""
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
    def from_dict(cls, data: Dict[str, Any]) -> 'CodegenResponse':
        received_at = data.get('received_at')
        if received_at and isinstance(received_at, str):
            received_at = datetime.fromisoformat(received_at)
        evaluated_at = data.get('evaluated_at')
        if evaluated_at and isinstance(evaluated_at, str):
            evaluated_at = datetime.fromisoformat(evaluated_at)
        return cls(
            challenge_id=data['challenge_id'],
            processing_time=data.get('processing_time'),
            node_id=data.get('node_id'),
            miner_hotkey=data.get('miner_hotkey'),
            response_id=data.get('response_id'),
            received_at=received_at,
            score=data.get('score'),
            evaluated=data.get('evaluated', False),
            evaluated_at=evaluated_at,
            response_patch=data.get('response_patch')
        )

# Get the absolute path to repos dir
validator_dir = Path(__file__).parents[1]
repos_dir = validator_dir / "repos"

SUPPORTED_CODEGEN_REPOS: Dict[str, Path] = {
    "mwaskom/seaborn": repos_dir / "seaborn",
    "pytest-dev/pytest":  repos_dir / "pytest",
}