from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel

from models.agent import Agent
from models.evaluation import Evaluation
from utils.system_metrics import SystemMetrics


class ValidatorStatus(str, Enum):
    available = "Available"
    screening = "Screening"
    evaluating = "Evaluating"
    paused = "Paused"


class ConnectedValidatorInfo(BaseModel):
    """Connected Validator Info used as the response
    schema for the /connected-validators-info endpoint
    """

    name: str
    hotkey: str
    time_connected: datetime
    status: ValidatorStatus

    time_last_heartbeat: Optional[datetime] = None
    system_metrics: Optional[SystemMetrics] = None

    evaluation: Optional[Evaluation] = None
    agent: Optional[Agent] = None
