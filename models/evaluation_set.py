import datetime
from enum import Enum
from uuid import UUID
from pydantic import BaseModel

from models.problem import ProblemDifficulty, ProblemSuiteName


class EvaluationSetGroup(str, Enum):
    screener_1 = "screener_1"
    screener_2 = "screener_2"
    validator = "validator"

    @staticmethod
    def from_validator_hotkey(validator_hotkey: str) -> "EvaluationSetGroup":
        if validator_hotkey.startswith("screener-1"):
            return EvaluationSetGroup.screener_1
        elif validator_hotkey.startswith("screener-2"):
            return EvaluationSetGroup.screener_2
        else:
            return EvaluationSetGroup.validator


class EvaluationSetProblem(BaseModel):
    set_id: int
    set_group: EvaluationSetGroup
    problem_name: str
    problem_suite_name: ProblemSuiteName
    created_at: datetime.datetime


class RawInfiniteSWEProblem(BaseModel):
    id: UUID
    repo: str
    instance_id: str
    base_commit: str
    patch: str
    test_patch: str
    problem_statement: str
    hints_text: str
    created_at: str
    version: str
    FAIL_TO_PASS: str
    PASS_TO_PASS: str
    environment_setup_commit: str
