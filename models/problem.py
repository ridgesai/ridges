from enum import Enum
from typing import List, Any
from pydantic import BaseModel



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



class Problem(BaseModel):
    name: str

    problem_statement: str
    solution_diff: str

    userdata: Any = None