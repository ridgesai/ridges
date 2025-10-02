import time
import uuid

from fiber import Keypair
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException



DEV_MODE = True



# List of whitelisted validators
WHITELISTED_VALIDATORS = [
    {"name": "RoundTable 21",         "hotkey": "5Djyacas3eWLPhCKsS3neNSJonzfxJmD3gcrMTFDc4eHsn62"},
    {"name": "Uncle Tao",             "hotkey": "5FF1rU17iEYzMYS7V59P6mK2PFtz9wDUoUKrpFd3yw1wBcfq"},
    {"name": "Yuma",                  "hotkey": "5Eho9y6iF5aTdKS28Awn2pKTd4dFsJ2o3shGtj1vjnLiaKJ1"},
    {"name": "Rizzo",                 "hotkey": "5GuRsre3hqm6WKWRCqVxXdM4UtGs457nDhPo9F5wvJ16Ys62"},
    {"name": "Ridges",                "hotkey": "5GgJptBaUiWwb8SQDinZ9rDQoVw47mgduXaCLHeJGTtA4JMS"},
    {"name": "Crucible Labs",         "hotkey": "5HmkM6X1D3W3CuCSPuHhrbYyZNBy2aGAiZy9NczoJmtY25H7"},
    {"name": "tao.bot",               "hotkey": "5E2LP6EnZ54m3wS8s1yPvD5c3xo71kQroBw7aUVK32TKeZ5u"},
    {"name": "Opentensor Foundation", "hotkey": "5FZ1BFw8eRMAFK5zwJdyefrsn51Lrm217WKbo3MmdFH65YRr"},
]

def is_validator_hotkey_whitelisted(validator_hotkey: str) -> bool:
    return validator_hotkey in [validator["hotkey"] for validator in WHITELISTED_VALIDATORS]

def validator_name_to_hotkey(validator_name: str) -> str:
    return next((validator["hotkey"] for validator in WHITELISTED_VALIDATORS if validator["name"] == validator_name), None)

def validator_hotkey_to_name(validator_hotkey: str) -> str:
    return next((validator["name"] for validator in WHITELISTED_VALIDATORS if validator["hotkey"] == validator_hotkey), None)



# TODO: Move to utils/bittensor.py
def check_signed_timestamp(timestamp: int, signed_timestamp: str, hotkey: str) -> bool:
    keypair = Keypair(ss58_address=hotkey)
    return keypair.verify(str(timestamp), bytes.fromhex(signed_timestamp))



router = APIRouter()



class ValidatorRegistrationRequest(BaseModel):
    timestamp: int
    signed_timestamp: str
    hotkey: str

class ValidatorRegistrationResponse(BaseModel):
    session_id: uuid.UUID

@router.post("/register")
async def validator_register(request: ValidatorRegistrationRequest) -> ValidatorRegistrationResponse:
    if not DEV_MODE:
        # Ensure that the hotkey is in the list of acceptable validator hotkeys
        if not is_validator_hotkey_whitelisted(request.hotkey):
            raise HTTPException(
                status_code=403,
                detail="The provided hotkey is not in the list of whitelisted validator hotkeys"
            )
    
    # Check if the signed timestamp is valid (i.e., matches the raw timestamp)
    if not check_signed_timestamp(request.timestamp, request.signed_timestamp, request.hotkey):
        raise HTTPException(
            status_code=401,
            detail="The provided signed timestamp does not match the provided timestamp"
        )

    # Ensure that the timestamp is within 1 minute of the current time
    if abs(int(request.timestamp) - int(time.time())) > 60:
        raise HTTPException(
            status_code=400,
            detail="The provided timestamp is not within 1 minute of the current time"
        )

    # All good, generate a random session ID
    # TODO: Maintain a map of session IDs and their associated validator objects
    session_id = uuid.uuid4()
    
    print(f"Registered validator ({validator_hotkey_to_name(request.hotkey)}/{request.hotkey}), Session ID: {session_id}")
    
    return ValidatorRegistrationResponse(session_id=session_id)