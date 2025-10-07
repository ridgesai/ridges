import time

from uuid import UUID, uuid4
from datetime import datetime
from pydantic import BaseModel
from typing import Dict, Optional
from fastapi.security import HTTPBearer
from loggers.logging_utils import get_logger
from utils.system_metrics import SystemMetrics
from utils.fiber import validate_signed_timestamp
from api.src.backend.entities import EvaluationRun
from fastapi import APIRouter, HTTPException, Depends

logger = get_logger(__name__)



DEV_MODE = True



# A connected validator
class Validator(BaseModel):
    name: str
    hotkey: str
    time_connected: datetime

    current_evaluation_id: Optional[UUID] = None

    time_last_heartbeat: Optional[datetime] = None
    system_metrics: SystemMetrics = SystemMetrics()

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



# /validator/register

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
    if not validate_signed_timestamp(request.timestamp, request.signed_timestamp, request.hotkey):
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
    session_id = uuid4()
    SESSION_ID_TO_VALIDATOR[session_id] = Validator(
        name=validator_hotkey_to_name(request.hotkey),
        hotkey=request.hotkey,
        time_connected=datetime.now()
    )
    
    logger.info(f"Registered validator ({validator_hotkey_to_name(request.hotkey)}/{request.hotkey}), Session ID: {session_id}")
    
    return ValidatorRegistrationResponse(session_id=session_id)



# /validator/request-evaluation

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



# /validator/heartbeat

class ValidatorHeartbeatRequest(BaseModel):
    system_metrics: SystemMetrics
class ValidatorHeartbeatResponse(BaseModel):
    pass

@router.post("/heartbeat")
async def validator_heartbeat(
    request: ValidatorHeartbeatRequest,
    validator: Validator = Depends(get_request_validator)
) -> ValidatorHeartbeatResponse:
    logger.info(f"Received heartbeat from{validator.name}/{validator.hotkey}")
    validator.time_last_heartbeat = datetime.now()
    validator.system_metrics = request.system_metrics
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
