from fastapi import HTTPException


class PaymentAlreadyUsedError(HTTPException):
    """Raised when a payment that has already been used for an agent upload is attempted to be used again."""

    def __init__(self):
        super().__init__(
            status_code=402,
            detail="Agent ID already exists for this payment information.",
        )
