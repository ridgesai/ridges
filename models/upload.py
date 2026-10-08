from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class UploadStatusResponse(BaseModel):
    """Common status/message shape for upload flows."""

    status: str = Field(..., description="Status of the upload operation")
    message: str = Field(..., description="Detailed message about the upload result")


class AgentUploadResponse(UploadStatusResponse):
    """Response model for successful agent upload"""

    agent_id: UUID = Field(..., description="Admitted (or replayed) agent ID")
    miner_hotkey: str = Field(..., description="Miner hotkey the agent was admitted for")
    miner_coldkey: Optional[str] = Field(None, description="Owning coldkey when known at admission time")


class UploadPriceResponse(BaseModel):
    """Current upload price of one competition. price_usd is already decayed to as_of."""

    amount_alpha_rao: int = Field(..., description="Amount of SN62 alpha to burn (in rao) at price_usd")
    payment_netuid: int = Field(..., description="Subnet whose alpha must be burned")
    set_id: int
    price_usd: float = Field(..., description="Price for a new quote right now")
    floor_usd: float
    target_per_hour: float
    half_life_minutes: float
    multiplier: float
    price_updated_at: datetime = Field(..., description="Time of the last bump or settings change")
    as_of: datetime = Field(..., description="Time price_usd was computed for")


class UploadPricePurchase(BaseModel):
    at: datetime
    price_usd: float = Field(..., description="Price the purchase was charged")


class UploadPriceHistoryResponse(BaseModel):

    set_id: int
    price_usd: float = Field(..., description="Current price, at as_of")
    as_of: datetime
    floor_usd: float
    half_life_minutes: float
    multiplier: float
    purchases: list[UploadPricePurchase] = Field(
        ..., description="Purchases since `since`, oldest first, led by the last one before it"
    )


class AgentCheckResponse(UploadStatusResponse):
    """Response model for successful agent upload preflight checks"""

    payment_method: Literal["burn", "credit"] = Field("burn", description="Payment method selected for this upload")
    quote_id: Optional[UUID] = Field(None, description="Quote ID to include when uploading or resuming")
    credit_id: Optional[UUID] = Field(None, description="One-shot upload credit to redeem")
    amount_alpha_rao: int = Field(..., description="Amount of SN62 alpha to burn (in rao)")
    payment_netuid: Optional[int] = Field(None, description="Subnet whose alpha must be burned")
    expires_at: Optional[datetime] = Field(None, description="Latest on-chain burn timestamp accepted for this quote")
    price_usd: Optional[float] = Field(None, description="Current upload price of the competition")
    balance_alpha_rao: Optional[int] = Field(None, description="The coldkey's burn balance in rao, spent at purchase")


class AgentDirectCheckResponse(AgentCheckResponse):
    """Direct upload preflight response with its authoritative competition."""

    set_id: int = Field(..., description="Competition selected for direct agent admission")


class ErrorResponse(BaseModel):
    """Error response model"""

    detail: str = Field(..., description="Error message describing what went wrong")


class PrepareUploadRequest(BaseModel):
    """Model for minting an upload quote/credit reservation for a ticket."""

    hotkey: str = Field(..., description="Miner hotkey ss58 address")
    public_key: str = Field(..., description="Public key of the miner hotkey in hex format")
    signature: str = Field(..., description="Hex signature over the prepare signing string")
    use_credit: bool = Field(False, description="Reserve an admin-granted upload credit instead of quoting a burn")
    credit_id: Optional[UUID] = Field(None, description="Specific upload credit ID for a retry")
    set_id: Optional[int] = Field(None, description="Competition the burn quote is for; required for burn quotes")


class TicketCheckRequest(BaseModel):
    """Model for validating an upload ticket blob."""

    ticket: str = Field(..., description="Upload ticket (ridges1...) minted by `ridges prepare-upload`")


class TicketCheckResponse(BaseModel):
    """Validity verdict for an upload ticket. Always HTTP 200 — validity is data, not an error."""

    valid: bool = Field(..., description="Whether the ticket can currently be redeemed")
    reason: Optional[str] = Field(
        None,
        description=(
            "Why the ticket is not redeemable: malformed_ticket, invalid_signature, owner_not_allowed, "
            "unknown_quote, already_redeemed, refunded, unknown_credit, credit_revoked, credit_expired, "
            "not_purchased, quote_cancelled, competition_not_accepting"
        ),
    )
    hotkey: Optional[str] = Field(None, description="Hotkey the ticket is bound to")
    funding: Optional[str] = Field(None, description="Ticket funding source: burn or credit")
    amount_alpha_rao: Optional[int] = Field(None, description="Alpha paid (0 for credit tickets)")
    expires_at: Optional[datetime] = Field(None, description="Credit expiry; null for burn tickets (no expiry)")
    redeemed_agent_id: Optional[UUID] = Field(None, description="Agent that already consumed this ticket's funding")
    set_id: Optional[int] = Field(None, description="Competition a competition-bound ticket belongs to")
    competition_state: Optional[str] = Field(None, description="State of that competition")


class OpenRouterKeysCheckRequest(BaseModel):
    """Request model for pre-validating OpenRouter keys before an upload."""

    openrouter_api_key: str = Field(..., description="OpenRouter runtime API key")
    openrouter_management_key: str = Field(..., description="OpenRouter management key")


class OpenRouterKeysCheckResponse(BaseModel):
    """Validity verdict for a pair of OpenRouter keys. HTTP 200 whether valid or not."""

    valid: bool = Field(..., description="Whether the key pair passed platform validation")
    reason: Optional[str] = Field(None, description="Human-readable reason when invalid")


class ConfirmPaymentRequest(BaseModel):
    """Burn receipt reported right after the burn lands. Signed over the canonical receipt."""

    quote_id: UUID
    payment_block_hash: str
    payment_extrinsic_index: int | str
    hotkey: str
    public_key: str
    signature: str


class CancelQuoteRequest(BaseModel):
    """Release an unburned quote. Signed by the quote's hotkey."""

    hotkey: str
    public_key: str
    signature: str


class PurchaseQuoteRequest(BaseModel):
    """Buy the upload a quote is for, from the coldkey's burn balance. Signed by the quote's hotkey."""

    hotkey: str
    public_key: str
    signature: str


class QuoteActionResponse(BaseModel):
    quote_id: UUID
    status: Literal["confirmed", "replayed", "cancelled", "purchased", "already_purchased", "legacy"]


class BalanceResponse(BaseModel):
    coldkey: str
    balance_alpha_rao: int
