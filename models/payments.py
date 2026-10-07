from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class Payment(BaseModel):
    payment_block_hash: str
    payment_extrinsic_index: str

    quote_id: Optional[UUID] = None
    upload_credit_id: Optional[UUID] = None
    agent_id: Optional[UUID] = None

    miner_hotkey: str
    miner_coldkey: str
    amount_rao: Optional[int] = None
    amount_alpha_rao: Optional[int] = None

    created_at: datetime


class PaymentQuote(BaseModel):
    quote_id: UUID
    miner_hotkey: str
    amount_rao: Optional[int] = None
    amount_alpha_rao: Optional[int] = None
    send_address: Optional[str] = None
    created_at: datetime
    expires_at: datetime
    set_id: Optional[int] = None
    price_usd: Optional[float] = None
    miner_coldkey: Optional[str] = None
    confirmed_at: Optional[datetime] = None
    confirmed_payment_block_hash: Optional[str] = None
    confirmed_payment_extrinsic_index: Optional[str] = None
    cancelled_at: Optional[datetime] = None
    is_legacy: bool = False
    purchased_at: Optional[datetime] = None
    redeemed_agent_id: Optional[UUID] = None
    refunded_at: Optional[datetime] = None
