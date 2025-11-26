from enum import Enum
from pydantic import BaseModel
from typing import Any, Optional



class ProblemTestCategory(str, Enum):
    default = 'default'
    pass_to_pass = 'pass_to_pass'
    fail_to_pass = 'fail_to_pass'

class ProblemTest(BaseModel):
    name: str
    category: ProblemTestCategory



class ProblemTestResultStatus(str, Enum):
    PASS = 'pass'
    FAIL = 'fail'
    SKIP = 'skip'

class ProblemTestResult(BaseModel):
    name: str
    category: ProblemTestCategory
    status: ProblemTestResultStatus



class ProblemDifficulty(str, Enum):
    EASY = 'easy'
    MEDIUM = 'medium'
    HARD = 'hard'
    IMPOSSIBLE = 'impossible'

class Problem(BaseModel):
    name: str
    difficulty: Optional[ProblemDifficulty] = None

    problem_statement: str
    solution_diff: str

    userdata: Any = None