from calendar import c
import time
import utils.logger as logger

from uuid import UUID, uuid4
from datetime import datetime
from pydantic import BaseModel
from typing import Dict, List, Optional
from fastapi.security import HTTPBearer
from models.evaluation import Evaluation
from utils.system_metrics import SystemMetrics
from utils.fiber import validate_signed_timestamp
from fastapi import Depends, Response, APIRouter, HTTPException
from api.queries.evaluations import create_new_evaluation_and_evaluation_runs
from api.queries.agents import get_next_agent_id_awaiting_evaluation_for_validator_hotkey
from models.evaluation_run import EvaluationRun, EvaluationRunStatus, EvaluationRunTestResult
from utils.validator_hotkeys import validator_hotkey_to_name, is_validator_hotkey_whitelisted
from api.queries.evaluation_runs import get_evaluation_run_by_id, update_evaluation_run_by_id



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

# Returns True if a validator with the given hotkey is currently registered
def is_validator_registered(validator_hotkey: str) -> bool:
    return validator_hotkey in [validator.hotkey for validator in SESSION_ID_TO_VALIDATOR.values()]



# Dependency to get the validator associated with the request
# Requires that the request has a valid "Authorization: Bearer <session_id>" header
# See validator_request_evaluation() and other endpoints for usage examples
async def get_request_validator(token: str = Depends(HTTPBearer())) -> Validator:
    # Make sure the session_id is a valid UUID
    try:
        session_id = UUID(token.credentials)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Invalid session ID format (expected a UUID)."
        )
    
    # Make sure the session_id is associated with a validator
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

    # Ensure that the validator is not already registered
    if is_validator_registered(request.hotkey):
        raise HTTPException(
            status_code=409,
            detail=f"There is already a validator connected with the given hotkey."
        )

    # Register the validator with a new session ID
    session_id = uuid4()
    SESSION_ID_TO_VALIDATOR[session_id] = Validator(
        name=validator_hotkey_to_name(request.hotkey),
        hotkey=request.hotkey,
        time_connected=datetime.now()
    )
    
    logger.info(f"Registered validator ({validator_hotkey_to_name(request.hotkey)}/{request.hotkey})")
    logger.info(f"\tSession ID: {session_id}")
    
    return ValidatorRegistrationResponse(session_id=session_id)



# /validator/request-evaluation

class ValidatorRequestEvaluationRequest(BaseModel):
    pass

class ValidatorRequestEvaluationResponse(BaseModel):
    evaluation: Evaluation
    evaluation_runs: List[EvaluationRun]

@router.post("/request-evaluation")
async def validator_request_evaluation(
    request: ValidatorRequestEvaluationRequest,
    validator: Validator = Depends(get_request_validator)
) -> ValidatorRequestEvaluationResponse:

    # Make sure the validator is not already running an evaluation
    if validator.current_evaluation_id is not None:
        raise HTTPException(
            status_code=409,
            detail=f"This validator is already running an evaluation, and validators may only run one evaluation at a time."
        )

    # Find the next agent awaiting an evaluation for this validator
    agent_id = await get_next_agent_id_awaiting_evaluation_for_validator_hotkey(validator.hotkey)
    if agent_id is None:
        # Nobody is awaiting an evaluation for this validator
        return Response(status_code=204)

    # Create a new evaluation and evaluation runs for this agent
    evaluation, evaluation_runs = await create_new_evaluation_and_evaluation_runs(agent_id, validator.hotkey)

    logger.info(f"Validator {validator.name}/{validator.hotkey} requested an evaluation")
    logger.info(f"\tAgent ID: {agent_id}")
    logger.info(f"\tEvaluation ID: {evaluation.evaluation_id}")
    logger.info(f"\t# of evaluation runs: {len(evaluation_runs)}")

    return ValidatorRequestEvaluationResponse(evaluation=evaluation, evaluation_runs=evaluation_runs)



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

    logger.info(f"Received heartbeat from {validator.name}/{validator.hotkey}")
    logger.info(f"\tSystem metrics: {request.system_metrics}")

    validator.time_last_heartbeat = datetime.now()
    validator.system_metrics = request.system_metrics
    
    return ValidatorHeartbeatResponse()



# /validator/update-evaluation-run

class ValidatorUpdateEvaluationRunRequest(BaseModel):
    evaluation_run_id: UUID
    updated_status: EvaluationRunStatus
    
    patch: Optional[str] = None
    test_results: Optional[List[EvaluationRunTestResult]] = None

    agent_logs: Optional[str] = None
    eval_logs: Optional[str] = None

    error_code: Optional[int] = None
    error_message: Optional[str] = None

class ValidatorUpdateEvaluationRunResponse(BaseModel):
    pass

@router.post("/update-evaluation-run")
async def update_evaluation_run(
    request: ValidatorUpdateEvaluationRunRequest,
    validator: Validator = Depends(get_request_validator)
) -> ValidatorUpdateEvaluationRunResponse:

    # TODO: Actually use the agent logs and eval logs when required

    # Make sure the validator is currently running an evaluation
    if validator.current_evaluation_id is None:
        raise HTTPException(
            status_code=409,
            detail=f"This validator is not currently running an evaluation, and therefore cannot update an evaluation run."
        )

    # Get the evaluation run with the provided evaluation_run_id
    evaluation_run = await get_evaluation_run_by_id(request.evaluation_run_id)

    # Make sure that the evaluation run actually exists
    if evaluation_run is None:
        raise HTTPException(
            status_code=404,
            detail=f"Evaluation run with ID {request.evaluation_run_id} does not exist."
        )

    # Make sure that the evaluation run is associated with the validator's current evaluation
    if evaluation_run.evaluation_id != validator.current_evaluation_id:
        raise HTTPException(
            status_code=403,
            detail=f"The evaluation run with ID {request.evaluation_run_id} is not associated with the validator's current evaluation."
        )



    # The logic differs based on the updated status of the evaluation run
    match (request.updated_status):
        case EvaluationRunStatus.pending:
            # There is no case where the validator should update an evaluation run to pending, since all evaluation runs start as pending
            raise HTTPException(
                status_code=400,
                detail="An evaluation run can never be updated to pending."
            )



        case EvaluationRunStatus.initializing_agent:
            # A validator may only update an evaluation run to initializing_agent if the evaluation run is currently in the pending status
            if evaluation_run.status != EvaluationRunStatus.pending:
                raise HTTPException(
                    status_code=400,
                    detail="An evaluation run can only be updated to initializing_agent if it is currently in the pending status."
                )

            # Update the evaluation run to initializing_agent
            evaluation_run.status = EvaluationRunStatus.initializing_agent
            evaluation_run.started_initializing_agent_at = datetime.now()



        case EvaluationRunStatus.running_agent:
            # A validator may only update an evaluation run to running_agent if the evaluation run is currently in the initializing_agent status
            if evaluation_run.status != EvaluationRunStatus.initializing_agent:
                raise HTTPException(
                    status_code=400,
                    detail="An evaluation run can only be updated to running_agent if it is currently in the initializing_agent status."
                )

            # Update the evaluation run to running_agent
            evaluation_run.status = EvaluationRunStatus.running_agent
            evaluation_run.started_running_agent_at = datetime.now()



        case EvaluationRunStatus.initializing_eval:
            # A validator may only update an evaluation run to initializing_eval if the evaluation run is currently in the running_agent status
            if evaluation_run.status != EvaluationRunStatus.running_agent:
                raise HTTPException(
                    status_code=400,
                    detail="An evaluation run can only be updated to initializing_eval if it is currently in the running_agent status."
                )

            # Make sure that the patch is provided
            if request.patch is None:
                raise HTTPException(
                    status_code=422,
                    detail="The patch is required when updating an evaluation run to initializing_eval."
                )
            
            # Make sure that the agent logs are provided
            if request.agent_logs is None:
                raise HTTPException(
                    status_code=422,
                    detail="The agent logs are required when updating an evaluation run to initializing_eval."
                )

            # Update the evaluation run to initializing_eval
            evaluation_run.status = EvaluationRunStatus.initializing_eval
            evaluation_run.patch = request.patch
            evaluation_run.started_initializing_eval_at = datetime.now()

        

        case EvaluationRunStatus.running_eval:
            # A validator may only update an evaluation run to running_eval if the evaluation run is currently in the initializing_eval status
            if evaluation_run.status != EvaluationRunStatus.initializing_eval:
                raise HTTPException(
                    status_code=400,
                    detail="An evaluation run can only be updated to running_eval if it is currently in the initializing_eval status."
                )

            # Update the evaluation run to running_eval
            evaluation_run.status = EvaluationRunStatus.running_eval
            evaluation_run.started_running_eval_at = datetime.now()



        case EvaluationRunStatus.finished:
            # A validator may only update an evaluation run to finished if the evaluation run is currently in the running_eval status
            if evaluation_run.status != EvaluationRunStatus.initializing_eval:
                raise HTTPException(
                    status_code=400,
                    detail="An evaluation run can only be updated to finished if it is currently in the running_eval status."
                )

            # Make sure the test results are provided
            if request.test_results is None:
                raise HTTPException(
                    status_code=422,
                    detail="The test results are required when updating an evaluation run to finished."
                )

            # Make sure the eval logs are provided
            if request.eval_logs is None:
                raise HTTPException(
                    status_code=422,
                    detail="The eval logs are required when updating an evaluation run to finished."
                )

            # Update the evaluation run to finished
            evaluation_run.status = EvaluationRunStatus.finished
            evaluation_run.finished_or_errored_at = datetime.now()
        


        case EvaluationRunStatus.error:
            # A validator may only update an evaluation run to error if the evaluation run is currently in the pending, initializing_agent, running_agent, initializing_eval, or running_eval status
            if evaluation_run.status not in [
                EvaluationRunStatus.pending,
                EvaluationRunStatus.initializing_agent,
                EvaluationRunStatus.running_agent,
                EvaluationRunStatus.initializing_eval,
                EvaluationRunStatus.running_eval
            ]:
                raise HTTPException(
                    status_code=400,
                    detail="An evaluation run can only be updated to error if it is currently in the pending, initializing_agent, running_agent, initializing_eval, or running_eval status."
                )

            # Make sure the error code is provided
            if request.error_code is None:
                raise HTTPException(
                    status_code=422,
                    detail="The error code is required when updating an evaluation run to error."
                )
            
            # Make sure the error message is provided
            if request.error_message is None:
                raise HTTPException(
                    status_code=422,
                    detail="The error message is required when updating an evaluation run to error."
                )
            
            # Update the evaluation run to error
            evaluation_run.status = EvaluationRunStatus.error
            evaluation_run.error_code = request.error_code
            evaluation_run.error_message = request.error_message
            evaluation_run.finished_or_errored_at = datetime.now()



    await update_evaluation_run_by_id(evaluation_run)

    logger.info(f"Updated evaluation run {request.evaluation_run_id} to {request.updated_status}")

    return ValidatorUpdateEvaluationRunResponse()