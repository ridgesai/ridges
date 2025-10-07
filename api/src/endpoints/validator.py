import time

from datetime import datetime
from typing import Dict, Optional

from uuid import UUID
from fiber import Keypair
from pydantic import BaseModel
from fastapi.security import HTTPBearer
from loggers.logging_utils import get_logger
from api.src.backend.entities import EvaluationRun
from fastapi import APIRouter, HTTPException, Depends
from validator_2.utils.system_metrics import ValidatorHeartbeatMetrics

logger = get_logger(__name__)



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
    return next((validator["hotkey"] for validator in WHITELISTED_VALIDATORS if validator["name"] == validator_name), 'unknown')

def validator_hotkey_to_name(validator_hotkey: str) -> str:
    return next((validator["name"] for validator in WHITELISTED_VALIDATORS if validator["hotkey"] == validator_hotkey), 'unknown')



# TODO: Move to utils/bittensor.py
def check_signed_timestamp(timestamp: int, signed_timestamp: str, hotkey: str) -> bool:
    try:
        keypair = Keypair(ss58_address=hotkey)
        return keypair.verify(str(timestamp), bytes.fromhex(signed_timestamp))
    except Exception as e:
        print(f"Error in check_signed_timestamp(timestamp={timestamp}, signed_timestamp={signed_timestamp}, hotkey={hotkey}): {e}")
        return False



# A connected validator
class Validator(BaseModel):
    name: str
    hotkey: str
    time_connected: datetime
    current_evaluation_id: Optional[UUID]

# Map of session IDs to validator objects
SESSION_ID_TO_VALIDATOR: Dict[UUID, Validator] = {}

# Dependency to get the validator associated with the request
# Requires that the request has a valid "Authorization: Bearer <session_id>" header
# See validator_request_evaluation() and other endpoints for usage examples
async def get_request_validator(token: str = Depends(HTTPBearer())) -> Validator:
    try:
        session_id = UUID(token.credentials)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Invalid session ID format (expected a UUID)."
        )
    
    if session_id not in SESSION_ID_TO_VALIDATOR:
        raise HTTPException(
            status_code=401,
            detail="Session ID not found or expired."
        )
    
    return SESSION_ID_TO_VALIDATOR[session_id]



router = APIRouter()



class ValidatorRegistrationRequest(BaseModel):
    timestamp: int
    signed_timestamp: str
    hotkey: str

class ValidatorRegistrationResponse(BaseModel):
    session_id: UUID

@router.post("/register")
async def validator_register(
    request: ValidatorRegistrationRequest
) -> ValidatorRegistrationResponse:

    if not DEV_MODE:
        # Ensure that the hotkey is in the list of acceptable validator hotkeys
        if not is_validator_hotkey_whitelisted(request.hotkey):
            raise HTTPException(
                status_code=403,
                detail="The provided hotkey is not in the list of whitelisted validator hotkeys."
            )
    
    # Check if the signed timestamp is valid (i.e., matches the raw timestamp)
    if not check_signed_timestamp(request.timestamp, request.signed_timestamp, request.hotkey):
        raise HTTPException(
            status_code=401,
            detail="The provided signed timestamp does not match the provided timestamp."
        )

    # Ensure that the timestamp is within 1 minute of the current time
    if abs(int(request.timestamp) - int(time.time())) > 60:
        raise HTTPException(
            status_code=400,
            detail="The provided timestamp is not within 1 minute of the current time."
        )

    # Register the validator with a new session ID
    session_id = uuid.uuid4()
    SESSION_ID_TO_VALIDATOR[session_id] = Validator(
        name=validator_hotkey_to_name(request.hotkey),
        hotkey=request.hotkey,
        time_connected=datetime.now()
    )
    
    logger.info(f"Registered validator ({validator_hotkey_to_name(request.hotkey)}/{request.hotkey}), Session ID: {session_id}")
    
    return ValidatorRegistrationResponse(session_id=session_id)



class ValidatorRequestEvaluationRequest(BaseModel):
    pass

class ValidatorRequestEvaluationResponse(BaseModel):
    foo: str

@router.post("/request-evaluation")
async def validator_request_evaluation(
    request: ValidatorRequestEvaluationRequest,
    validator: Validator = Depends(get_request_validator)
) -> ValidatorRequestEvaluationResponse:

    logger.info(f"{validator.name}/{validator.hotkey} is requesting an evaluation")
    return ValidatorRequestEvaluationResponse(foo="bar")


@router.post("/heartbeat")
async def validator_heartbeat(
    request: ValidatorHeartbeatMetrics,
    validator: Validator = Depends(get_request_validator)
) -> None:
    logger.info(f"{validator.name}/{validator.hotkey} has sent heartbeat")
    pass


def get_evaluation_id_for_run(run_id: UUID) -> UUID:
    # TODO: Check db for evaluation id associated with run
    pass


def write_evaluation_run_to_db(evaluation_run: EvaluationRun) -> None:
    # TODO: Write evaluation run to db
    pass


@router.post("/update-evaluation-run")
async def update_evaluation_run(
    evaluation_run: EvaluationRun,
    validator: Validator = Depends(get_request_validator)
) -> None:
    if validator.current_evaluation_id is None:
        raise HTTPException(
            status_code=409,
            detail=f"Validator {validator.hotkey} is not running an evaluation."
        )

    if evaluation_run.evaluation_id != validator.current_evaluation_id:
        raise HTTPException(
            status_code=403,
            detail=f"Evaluation IDs do not match. Evaluation id: {evaluation_run.evaluation_id}, current validator's ({validator.hotkey} evaluation is: {validator.current_evaluation_id}"
        )

    db_evaluation_id = get_evaluation_id_for_run(evaluation_run.run_id)
    if db_evaluation_id != validator.current_evaluation_id:
        raise HTTPException(
            status_code=400,
            detail=f"Evaluation ID mismatch: the provided evaluation ID ({evaluation_run.evaluation_id}) for run {evaluation_run.run_id} does not match the evaluation ID in the database ({db_evaluation_id})"
        )

    logger.info(f"{validator.name}/{validator.hotkey} has sent heartbeat")
    write_evaluation_run_to_db(evaluation_run)
    pass
