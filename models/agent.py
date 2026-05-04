from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class AgentStatus(str, Enum):
    screening_1 = "screening_1"
    failed_screening_1 = "failed_screening_1"
    screening_2 = "screening_2"
    failed_screening_2 = "failed_screening_2"
    evaluating = "evaluating"
    finished = "finished"


class AgentBase(BaseModel):
    miner_hotkey: str

    name: str
    version_num: int

    status: AgentStatus

    created_at: datetime
    ip_address: Optional[str] = None


class Agent(AgentBase):
    agent_id: UUID


class AgentCreate(AgentBase):
    """Schema used to create a new agent."""

    # Hash of the block containing the payment extrinsic associated with this
    # agent upload
    payment_block_hash: str
    # Index of the payment extrinsic within the block
    payment_extrinsic_index: str
    pass


class PossiblyBenchmarkAgent(Agent):
    is_benchmark_agent: bool
    benchmark_description: Optional[str] = None


# TODO ADAM: need to look into this more


class BenchmarkAgentScored(Agent):
    benchmark_description: Optional[str] = None

    set_id: int
    approved: bool
    validator_count: int
    final_score: float


class AgentScored(Agent):
    set_id: int
    approved: bool
    validator_count: int
    final_score: float
