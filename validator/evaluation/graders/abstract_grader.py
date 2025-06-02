from abc import ABC, abstractmethod
from typing import Dict, List
from validator.challenge.codegen.challenge import CodegenChallenge
from validator.challenge.codegen.response import CodegenResponse
from validator.challenge.base import ValidationResult

class GraderInterface(ABC):
    def grade(self, problem: CodegenChallenge, responses: List[CodegenResponse]) -> List[ValidationResult]:
        raise NotImplementedError("GraderInterface.grade() must be overridden")