import docker

from typing import Any, Dict, Optional
from pydantic import BaseModel, ConfigDict
from models.evaluation_run import EvaluationRunErrorCode



class Sandbox(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True) # Because of docker.models.containers.Container

    name: str
    temp_dir: str
    container: docker.models.containers.Container
    timeout_seconds: Optional[int]

class SandboxResult(BaseModel):
    success: bool

    # if success
    output: Any = None

    # if not success
    error: Optional[str] = None
    traceback: Optional[str] = None

class SandboxResultWithLogs(SandboxResult):
    logs: str



# Can be raised by some methods in ProblemSuite. These exceptions should be
# caught and handled by the validator.
#
# The extra field is used to pass along additional information to the
# validator. For example, if an exception occurs while running the agent
# sandbox, the evaluator can pass along the partial agent logs to the
# validator. The only keys that could be in the extra field are "agent_logs" or
# "eval_logs".
class EvaluationRunException(Exception):
    def __init__(self, error_code: EvaluationRunErrorCode, error_message: str, *, extra: Optional[Dict[str, Any]] = None):
        super().__init__(error_message)
        self.error_code = error_code
        self.error_message = error_message
        self.extra = extra