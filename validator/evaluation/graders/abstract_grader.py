from abc import ABC, abstractmethod
from typing import Dict, List, TYPE_CHECKING

if TYPE_CHECKING:
    from validator.challenge.codegen.response import CodegenResponse

class GraderInterface(ABC):
    @abstractmethod
    def grade(self, responses: List['CodegenResponse']) -> Dict[str, float]:
        """Grade a list of responses and return scores by hotkey."""
        raise NotImplementedError("GraderInterface.grade() must be overridden")