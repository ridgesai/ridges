from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class UploadCredit(BaseModel):
    credit_id: UUID
    miner_hotkey: str
    reason: str
    granted_by: str
    grant_reference: Optional[str] = None
    granted_at: datetime
    expires_at: Optional[datetime] = None
    redeemed_at: Optional[datetime] = None
    redeemed_agent_id: Optional[UUID] = None
    revoked_at: Optional[datetime] = None
    revoked_by: Optional[str] = None
    revoke_reason: Optional[str] = None
