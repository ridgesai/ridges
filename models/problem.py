from enum import Enum
from typing import List
from pydantic import BaseModel



class ProblemTestCategory(str, Enum):
    default = 'default'
    pass_to_pass = 'pass_to_pass'
    fail_to_pass = 'fail_to_pass'

class ProblemTest(BaseModel):
    name: str
    category: ProblemTestCategory



class ProblemTestResultStatus(str, Enum):
    passed = 'passed'
    failed = 'failed'
    skipped = 'skipped'

class ProblemTestResult(BaseModel):
    name: str
    category: ProblemTestCategory
    status: ProblemTestResultStatus



class Problem(BaseModel):
    name: str
    problem_statement: str
    solution_diff: str
    tests: List[ProblemTest]