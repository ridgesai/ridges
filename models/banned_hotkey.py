from datetime import datetime
from pydantic import BaseModel



class BannedHotkey(BaseModel):
    miner_hotkey: str
    banned_reason: str
    banned_at: datetime
