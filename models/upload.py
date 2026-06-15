from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class AgentUploadResponse(BaseModel):
    """Response model for successful agent upload"""

    status: str = Field(..., description="Status of the upload operation")
    message: str = Field(..., description="Detailed message about the upload result")


class UploadPriceResponse(BaseModel):
    """Response model for upload pricing"""

    amount_rao: int = Field(..., description="Amount to send for evaluation (in RAO)")
    send_address: str = Field(..., description="TAO address to send evaluation payment to")


class AgentCheckResponse(AgentUploadResponse):
    """Response model for successful agent upload preflight checks"""

    quote_id: UUID = Field(..., description="Quote ID to include when uploading or resuming")
    amount_rao: int = Field(..., description="Amount to send for evaluation (in RAO)")
    send_address: str = Field(..., description="TAO address to send evaluation payment to")
    expires_at: datetime = Field(..., description="Latest on-chain payment timestamp accepted for this quote")


class ErrorResponse(BaseModel):
    """Error response model"""

    detail: str = Field(..., description="Error message describing what went wrong")
