from datetime import datetime

from pydantic import BaseModel


class BannedColdkey(BaseModel):
    miner_coldkey: str
    banned_reason: str
    banned_at: datetime
