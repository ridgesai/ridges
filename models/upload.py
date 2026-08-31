from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class AgentUploadResponse(BaseModel):
    """Response model for successful agent upload"""

    status: str = Field(..., description="Status of the upload operation")
    message: str = Field(..., description="Detailed message about the upload result")


class UploadPriceResponse(BaseModel):
    """Response model for upload pricing"""

    amount_alpha_rao: int = Field(..., description="Amount of SN62 alpha to burn (in rao)")
    payment_netuid: int = Field(..., description="Subnet whose alpha must be burned")


class AgentCheckResponse(AgentUploadResponse):
    """Response model for successful agent upload preflight checks"""

    payment_method: Literal["burn", "credit"] = Field("burn", description="Payment method selected for this upload")
    quote_id: Optional[UUID] = Field(None, description="Quote ID to include when uploading or resuming")
    credit_id: Optional[UUID] = Field(None, description="One-shot upload credit to redeem")
    amount_alpha_rao: int = Field(..., description="Amount of SN62 alpha to burn (in rao)")
    payment_netuid: Optional[int] = Field(None, description="Subnet whose alpha must be burned")
    expires_at: Optional[datetime] = Field(None, description="Latest on-chain burn timestamp accepted for this quote")


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
            "unknown_quote, already_redeemed, refunded, unknown_credit, credit_revoked, credit_expired"
        ),
    )
    hotkey: Optional[str] = Field(None, description="Hotkey the ticket is bound to")
    funding: Optional[str] = Field(None, description="Ticket funding source: burn or credit")
    amount_alpha_rao: Optional[int] = Field(None, description="Alpha paid (0 for credit tickets)")
    expires_at: Optional[datetime] = Field(None, description="Credit expiry; null for burn tickets (no expiry)")
    redeemed_agent_id: Optional[UUID] = Field(None, description="Agent that already consumed this ticket's funding")


class OpenRouterKeysCheckRequest(BaseModel):
    """Request model for pre-validating OpenRouter keys before an upload."""

    openrouter_api_key: str = Field(..., description="OpenRouter runtime API key")
    openrouter_management_key: str = Field(..., description="OpenRouter management key")


class OpenRouterKeysCheckResponse(BaseModel):
    """Validity verdict for a pair of OpenRouter keys. HTTP 200 whether valid or not."""

    valid: bool = Field(..., description="Whether the key pair passed platform validation")
    reason: Optional[str] = Field(None, description="Human-readable reason when invalid")
