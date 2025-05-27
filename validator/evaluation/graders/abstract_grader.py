from abc import ABC
from typing import List

from validator.challenge.challenge_types import CodegenResponse, GeneratedCodegenProblem, ValidationResult

class GraderInterface(ABC):
    def grade(self, problem: GeneratedCodegenProblem, responses: List[CodegenResponse]) -> List[ValidationResult]:
        raise NotImplementedError("GraderInterface.grade() must be overridden")