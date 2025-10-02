import uuid

from fastapi import APIRouter
from pydantic import BaseModel



router = APIRouter()



class ValidatorRegistrationRequest(BaseModel):
    signed_timestamp: str
    hotkey: str

class ValidatorRegistrationResponse(BaseModel):
    session_id: uuid.UUID

@router.post("/register")
async def validator_register(request: ValidatorRegistrationRequest) -> ValidatorRegistrationResponse:
    # TODO
    return ValidatorRegistrationResponse(session_id=uuid.uuid4())