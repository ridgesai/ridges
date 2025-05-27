from typing import List

from validator.challenge.challenge_types import CodegenResponse, GeneratedCodegenProblem, ValidationResult
from .abstract_grader import GraderInterface

class FloatGrader(GraderInterface):
    def grade(self, problem: GeneratedCodegenProblem, responses: List[CodegenResponse]) -> List[ValidationResult]:
        return []