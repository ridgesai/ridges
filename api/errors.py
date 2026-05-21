from typing import Optional

from fastapi import HTTPException


class PaymentAlreadyUsedError(HTTPException):
    """Raised when a payment that has already been used for an agent upload is attempted to be used again."""

    def __init__(self):
        super().__init__(
            status_code=402,
            detail="Agent ID already exists for this payment information.",
        )


class PaymentRefunded(HTTPException):
    """Raised when a payment that was refunded is being used for an agent upload."""

    def __init__(self):
        super().__init__(
            status_code=402,
            detail="This payment has been refunded and cannot be used for agent upload.",
        )


class PlatformFrozenError(HTTPException):
    """Raised when uploads are frozen via DISALLOW_UPLOADS. Not recorded as an upload attempt."""

    def __init__(self, reason: Optional[str] = "Uploads are currently disabled"):
        super().__init__(status_code=503, detail=reason)
