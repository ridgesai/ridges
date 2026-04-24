import enum


class AgentStatus(str, enum.Enum):
    screening_1 = "screening_1"
    failed_screening_1 = "failed_screening_1"
    screening_2 = "screening_2"
    failed_screening_2 = "failed_screening_2"
    evaluating = "evaluating"
    finished = "finished"


class EvaluationSetGroup(str, enum.Enum):
    screener_1 = "screener_1"
    screener_2 = "screener_2"
    validator = "validator"


class EvaluationRunStatus(str, enum.Enum):
    pending = "pending"
    initializing_agent = "initializing_agent"
    running_agent = "running_agent"
    initializing_eval = "initializing_eval"
    running_eval = "running_eval"
    finished = "finished"
    error = "error"


class EvaluationRunLogType(str, enum.Enum):
    agent = "agent"
    eval = "eval"


class EvaluationStatus(str, enum.Enum):
    running = "running"
    success = "success"
    failure = "failure"
