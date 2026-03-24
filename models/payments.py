from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class Payment(BaseModel):
    payment_block_hash: str
    payment_extrinsic_index: str

    agent_id: UUID

    miner_hotkey: str
    miner_coldkey: str
    amount_rao: int

    created_at: datetime
