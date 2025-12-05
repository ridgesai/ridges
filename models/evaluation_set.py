import datetime

from enum import Enum
from pydantic import BaseModel



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
    created_at: datetime.datetime