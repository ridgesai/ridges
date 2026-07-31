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

    amount_alpha_rao: int = Field(..., description="Amount of SN62 alpha to burn (in 1e9 units)")
    payment_netuid: int = Field(..., description="Subnet whose alpha must be burned")


class AgentCheckResponse(AgentUploadResponse):
    """Response model for successful agent upload preflight checks"""

    payment_method: Literal["burn", "credit"] = Field("burn", description="Payment method selected for this upload")
    quote_id: Optional[UUID] = Field(None, description="Quote ID to include when uploading or resuming")
    credit_id: Optional[UUID] = Field(None, description="One-shot upload credit to redeem")
    amount_alpha_rao: int = Field(..., description="Amount of SN62 alpha to burn (in 1e9 units)")
    payment_netuid: Optional[int] = Field(None, description="Subnet whose alpha must be burned")
    expires_at: Optional[datetime] = Field(None, description="Latest on-chain burn timestamp accepted for this quote")


class ErrorResponse(BaseModel):
    """Error response model"""

    detail: str = Field(..., description="Error message describing what went wrong")
