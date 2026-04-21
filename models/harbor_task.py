"""Models for the remote Harbor execution specs stored in evaluation sets."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel

from models.problem import ProblemDifficulty


def normalize_metadata_label(value: object) -> str | None:
    """Return a stripped label, or ``None`` if the value is not usable."""
    if not isinstance(value, str):
        return None

    normalized = value.strip()
    return normalized or None


class HarborRemoteTaskExecutionSpec(BaseModel):
    """A Harbor task stored as a pre-materialized archive in S3.

    The task is identified by its S3 key and content digest. Validators
    download the archive via a presigned URL provided at evaluation-assignment
    time.
    """

    kind: Literal["harbor_remote_task"]
    dataset_name: str
    task_name: str
    s3_key: str
    task_digest: str
    agent_timeout_sec: float | None = None
    package_name: str | None = None
    benchmark_family: str | None = None
    problem_suite_name: str | None = None
    problem_difficulty: ProblemDifficulty | None = None


class HarborExecutionSpecMetadata(BaseModel):
    """Reporting fields pulled out of an execution spec."""

    benchmark_family: str | None = None
    problem_suite_name: str | None = None
    problem_difficulty: ProblemDifficulty | None = None


def _parse_problem_difficulty(value: object) -> ProblemDifficulty | None:
    """Parse a difficulty label into the canonical enum."""
    if isinstance(value, ProblemDifficulty):
        return value

    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {difficulty.value for difficulty in ProblemDifficulty}:
            return ProblemDifficulty(normalized)
        if "<15" in normalized or "< 15" in normalized:
            return ProblemDifficulty.EASY
        if "15 min" in normalized:
            return ProblemDifficulty.EASY
        if "1-4 hour" in normalized or "1 - 4 hour" in normalized:
            return ProblemDifficulty.MEDIUM
        if "hour" in normalized:
            return ProblemDifficulty.HARD

    return None


def _execution_spec_mapping(execution_spec: object) -> Mapping[str, object] | None:
    if isinstance(execution_spec, HarborRemoteTaskExecutionSpec):
        return execution_spec.model_dump(mode="python")
    if isinstance(execution_spec, Mapping):
        return execution_spec
    return None


def read_execution_spec_metadata(
    execution_spec: object,
    *,
    fallback_benchmark_family: object = None,
    fallback_problem_suite_name: object = None,
    fallback_problem_difficulty: object = None,
) -> HarborExecutionSpecMetadata | None:
    """Read reporting metadata from an execution spec, falling back when needed."""
    spec_data = _execution_spec_mapping(execution_spec) or {}

    benchmark_family = normalize_metadata_label(spec_data.get("benchmark_family")) or normalize_metadata_label(
        fallback_benchmark_family
    )
    problem_suite_name = (
        normalize_metadata_label(spec_data.get("problem_suite_name"))
        or normalize_metadata_label(fallback_problem_suite_name)
        or benchmark_family
    )
    problem_difficulty = _parse_problem_difficulty(spec_data.get("problem_difficulty")) or _parse_problem_difficulty(
        fallback_problem_difficulty
    )

    if benchmark_family is None and problem_suite_name is None and problem_difficulty is None:
        return None

    return HarborExecutionSpecMetadata(
        benchmark_family=benchmark_family,
        problem_suite_name=problem_suite_name,
        problem_difficulty=problem_difficulty,
    )
