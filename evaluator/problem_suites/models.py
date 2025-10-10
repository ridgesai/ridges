from typing import Any, Dict, Optional
from models.evaluation_run import EvaluationRunErrorCode



# Can be raised by some methods in ProblemSuite. These exceptions should be
# caught and handled by the validator.
#
# The extra field is used to pass along additional information to the
# validator. For example, if an exception occurs while running the agent
# sandbox, the validator can pass along the partial agent logs to the
# validator. The only keys that could be in the extra field are "agent_logs" or
# "eval_logs".
class EvaluationRunException(Exception):
    def __init__(self, error_code: EvaluationRunErrorCode, error_message: str, *, extra: Optional[Dict[str, Any]] = None):
        super().__init__(error_message)
        self.error_code = error_code
        self.error_message = error_message
        self.extra = extra