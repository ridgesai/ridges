from dataclasses import is_dataclass, dataclass, asdict, fields
from pathlib import Path
from textwrap import dedent
from typing import Any, Dict, Type, TypeVar, Union, get_origin, get_args
from typing import List, Optional

from pydantic import BaseModel

T = TypeVar('T')

# ================== Utils to for dataclass <-> dict parsing ===========================
def dict_to_dataclass_or_basemodel(cls: Type[T], data: Dict[str, Any]) -> T:
    """Recursively converts a dictionary into a dataclass or BaseModel instance, handling nested and optional fields."""
    if is_dataclass(cls):
        init_kwargs = {}
        for field in fields(cls):
            field_name = field.name
            field_type = field.type
            field_value = data.get(field_name, None)  # Default to None if key is missing

            # Resolve Optional and Union types
            if get_origin(field_type) is Union:
                # Handle Optional (Union[X, None]) by extracting the actual type
                actual_types = get_args(field_type)
                if len(actual_types) == 2 and type(None) in actual_types:
                    actual_type = next(t for t in actual_types if t is not type(None))
                else:
                    actual_type = None
            else:
                actual_type = field_type

            # Check if the actual type is a dataclass or BaseModel
            if actual_type and isinstance(actual_type, type):
                if is_dataclass(actual_type):
                    init_kwargs[field_name] = dict_to_dataclass_or_basemodel(actual_type, field_value) if field_value else None
                elif issubclass(actual_type, BaseModel):
                    init_kwargs[field_name] = actual_type(**field_value) if field_value else None
                else:
                    init_kwargs[field_name] = field_value
            else:
                init_kwargs[field_name] = field_value

        return cls(**init_kwargs)
    elif issubclass(cls, BaseModel):
        return cls(**data)
    else:
        raise TypeError(f"{cls} is neither a dataclass nor a BaseModel.")


def convert_to_obj(data: Any) -> Any:
    if is_dataclass(data):
        return {k: convert_to_obj(v) for k, v in asdict(data).items()}
    elif isinstance(data, BaseModel):
        return {k: convert_to_obj(v) for k, v in data.dict().items()}
    elif isinstance(data, list):
        return [convert_to_obj(item) for item in data]
    elif isinstance(data, dict):
        return {k: convert_to_obj(v) for k, v in data.items()}
    return data
# ========================================================================================

@dataclass
class IngestionHeuristics:
    min_files_to_consider_dir_for_problems: int
    min_file_content_len: int


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
class ValidatorModelStats:
    input_tokens: int
    output_tokens: int
    cost: float


@dataclass
class GeneratedProblemStatement:
    problem_uuid: str
    prompt: str
    model: str
    problem_statement: str
    dynamic_checklist: List[str]
    context_files: List[str]
    model_stats: Optional[ValidatorModelStats] = None

    def to_detailed_format(self) -> str:
        context_files_string = ""
        for i, file in enumerate(self.context_files):
            context_files_string += f"# File {i} used to solve the problem: {file}"
        return dedent(f"""
        Problem Statement: {self.problem_statement}
        Checklist of items to consider: {self.dynamic_checklist}
        {context_files_string}
        """)


@dataclass
class UnsolvedIssue:
    desc: str
    local_code_path: Path
    env_setup_path: Path


class MinerModelStats(BaseModel):
    api_calls: int
    instance_cost: float
    tokens_received: int
    tokens_sent: int
    total_cost: float


@dataclass
class IssueSolution:
    patch: str
    model_stats: Optional[MinerModelStats] = None
    exit_status: Optional[str] = None
