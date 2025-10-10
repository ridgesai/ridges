import re
import time
from datetime import datetime
from typing import Dict, List, Optional
from uuid import UUID, uuid4

from fastapi import Depends, APIRouter, HTTPException
from fastapi.security import HTTPBearer
from pydantic import BaseModel

import api.config as config
import utils.logger as logger
from api.queries.agent import get_agent_by_id, get_next_agent_id_awaiting_evaluation_for_validator_hotkey
from api.queries.evaluation import get_evaluation_by_id, create_new_evaluation_and_evaluation_runs, \
    get_all_evaluation_runs_for_evaluation_id, mark_running_evaluation_runs_as_errored, mark_evaluation_as_finished
from api.queries.evaluation_run import get_evaluation_run_by_id, update_evaluation_run_by_id
from models.agent import Agent
from models.evaluation import Evaluation
from models.evaluation_run import EvaluationRunStatus
from models.problem import ProblemTestResult
from utils.fiber import validate_signed_timestamp
from utils.s3 import download_text_file_from_s3
from utils.system_metrics import SystemMetrics
from utils.validator_hotkeys import validator_hotkey_to_name, is_validator_hotkey_whitelisted


# A connected validator
class Validator(BaseModel):
    session_id: UUID

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



# /validator/register-as-validator

class ValidatorRegistrationRequest(BaseModel):
    timestamp: int
    signed_timestamp: str
    hotkey: str

class ValidatorRegistrationResponse(BaseModel):
    session_id: UUID

@router.post("/register-as-validator")
async def validator_register_as_validator(
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
        session_id=session_id,
        name=validator_hotkey_to_name(request.hotkey),
        hotkey=request.hotkey,
        time_connected=datetime.now()
    )
    
    logger.info(f"Validator '{validator_hotkey_to_name(request.hotkey)}' ({request.hotkey}) was registered")
    logger.info(f"  Session ID: {session_id}")
    
    return ValidatorRegistrationResponse(session_id=session_id)



# /validator/register-as-screener

class ScreenerRegistrationRequest(BaseModel):
    name: str
    password: str

class ScreenerRegistrationResponse(BaseModel):
    session_id: UUID

@router.post("/register-as-screener")
async def validator_register_as_screener(
    request: ScreenerRegistrationRequest
) -> ScreenerRegistrationResponse:

    # Ensure that the name is in the format screener-CLASS-NUM
    if not re.match(r"screener-\d-\d+", request.name):
        raise HTTPException(
            status_code=400,
            detail="The provided name is not in the format screener-CLASS-NUM."
        )
    
    # Ensure that the CLASS is either 1 or 2
    screener_class = request.name.split("-")[1]
    if screener_class != "1" and screener_class != "2":
        raise HTTPException(
            status_code=400,
            detail="The screener class must be either 1 or 2."
        )
    
    # Ensure that the password is correct
    if request.password != config.SCREENER_PASSWORD:
        raise HTTPException(
            status_code=403,
            detail="The provided password is incorrect."
        )

    # Ensure that the screener is not already registered
    if is_validator_registered(request.name):
        raise HTTPException(
            status_code=409,
            detail=f"There is already a screener connected with the given name."
        )

    # Register the screener with a new session ID
    session_id = uuid4()
    SESSION_ID_TO_VALIDATOR[session_id] = Validator(
        session_id=session_id,
        name=request.name,
        hotkey=request.name,
        time_connected=datetime.now()
    )
    
    logger.info(f"Screener {request.name} was registered")
    logger.info(f"  Session ID: {session_id}")
    
    return ScreenerRegistrationResponse(session_id=session_id)



# /validator/request-evaluation

class StrippedEvaluationRun(BaseModel):
    evaluation_run_id: UUID
    problem_name: str

class ValidatorRequestEvaluationRequest(BaseModel):
    pass

class ValidatorRequestEvaluationResponse(BaseModel):
    agent_code: str
    evaluation_runs: List[StrippedEvaluationRun]

@router.post("/request-evaluation")
async def validator_request_evaluation(
    request: ValidatorRequestEvaluationRequest,
    validator: Validator = Depends(get_request_validator)
) -> Optional[ValidatorRequestEvaluationResponse]:

    # Make sure the validator is not already running an evaluation
    if validator.current_evaluation_id is not None:
        raise HTTPException(
            status_code=409,
            detail=f"This validator is already running an evaluation, and validators may only run one evaluation at a time."
        )

    # Find the next agent awaiting an evaluation from this validator
    agent_id = await get_next_agent_id_awaiting_evaluation_for_validator_hotkey(validator.hotkey)
    if agent_id is None:
        return None

    # Create a new evaluation and evaluation runs for this agent & validator
    evaluation, evaluation_runs = await create_new_evaluation_and_evaluation_runs(agent_id, validator.hotkey)
    validator.current_evaluation_id = evaluation.evaluation_id



    logger.info(f"Validator '{validator.name}' requested an evaluation")
    logger.info(f"  Agent ID: {agent_id}")
    logger.info(f"  Evaluation ID: {evaluation.evaluation_id}")
    logger.info(f"  # of evaluation runs: {len(evaluation_runs)}")

    agent_code = await download_text_file_from_s3(f"{agent_id}/agent.py")
    stripped_evaluation_runs = [StrippedEvaluationRun(evaluation_run_id=evaluation_run.evaluation_run_id, problem_name=evaluation_run.problem_name) for evaluation_run in evaluation_runs]

    return ValidatorRequestEvaluationResponse(agent_code=agent_code, evaluation_runs=stripped_evaluation_runs)



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

    # TODO: uncomment
    # logger.info(f"Validator '{validator.name}' sent a heartbeat")
    # logger.info(f"  System metrics: {request.system_metrics}")

    validator.time_last_heartbeat = datetime.now()
    validator.system_metrics = request.system_metrics
    
    return ValidatorHeartbeatResponse()



# /validator/update-evaluation-run

class ValidatorUpdateEvaluationRunRequest(BaseModel):
    evaluation_run_id: UUID
    updated_status: EvaluationRunStatus
    
    patch: Optional[str] = None
    test_results: Optional[List[ProblemTestResult]] = None

    agent_logs: Optional[str] = None
    eval_logs: Optional[str] = None

    error_code: Optional[int] = None
    error_message: Optional[str] = None

class ValidatorUpdateEvaluationRunResponse(BaseModel):
    pass

@router.post("/update-evaluation-run")
async def validator_update_evaluation_run(
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
    match request.updated_status:
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
                    detail=f"An evaluation run can only be updated to initializing_agent if it is currently in the pending status. The current status of evaluation run {request.evaluation_run_id} is {evaluation_run.status}."
                )

            # Update the evaluation run to initializing_agent
            evaluation_run.status = EvaluationRunStatus.initializing_agent
            evaluation_run.started_initializing_agent_at = datetime.now()



        case EvaluationRunStatus.running_agent:
            # A validator may only update an evaluation run to running_agent if the evaluation run is currently in the initializing_agent status
            if evaluation_run.status != EvaluationRunStatus.initializing_agent:
                raise HTTPException(
                    status_code=400,
                    detail=f"An evaluation run can only be updated to running_agent if it is currently in the initializing_agent status. The current status of evaluation run {request.evaluation_run_id} is {evaluation_run.status}."
                )

            # Update the evaluation run to running_agent
            evaluation_run.status = EvaluationRunStatus.running_agent
            evaluation_run.started_running_agent_at = datetime.now()



        case EvaluationRunStatus.initializing_eval:
            # A validator may only update an evaluation run to initializing_eval if the evaluation run is currently in the running_agent status
            if evaluation_run.status != EvaluationRunStatus.running_agent:
                raise HTTPException(
                    status_code=400,
                    detail=f"An evaluation run can only be updated to initializing_eval if it is currently in the running_agent status. The current status of evaluation run {request.evaluation_run_id} is {evaluation_run.status}."
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
                    detail=f"An evaluation run can only be updated to running_eval if it is currently in the initializing_eval status. The current status of evaluation run {request.evaluation_run_id} is {evaluation_run.status}."
                )

            # Update the evaluation run to running_eval
            evaluation_run.status = EvaluationRunStatus.running_eval
            evaluation_run.started_running_eval_at = datetime.now()



        case EvaluationRunStatus.finished:
            # A validator may only update an evaluation run to finished if the evaluation run is currently in the running_eval status
            if evaluation_run.status != EvaluationRunStatus.running_eval:
                raise HTTPException(
                    status_code=400,
                    detail=f"An evaluation run can only be updated to finished if it is currently in the running_eval status. The current status of evaluation run {request.evaluation_run_id} is {evaluation_run.status}."
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
                    detail=f"An evaluation run can only be updated to error if it is currently in the pending, initializing_agent, running_agent, initializing_eval, or running_eval status.  The current status of evaluation run {request.evaluation_run_id} is {evaluation_run.status}."
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

    logger.info(f"Validator '{validator.name}' updated an evaluation run")
    logger.info(f"  Evaluation run ID: {request.evaluation_run_id}")
    logger.info(f"  Updated status: {request.updated_status}")

    return ValidatorUpdateEvaluationRunResponse()



# /validator/disconnect

class ValidatorDisconnectRequest(BaseModel):
    reason: str
class ValidatorDisconnectResponse(BaseModel):
    pass

@router.post("/disconnect")
async def validator_disconnect(
    request: ValidatorDisconnectRequest,
    validator: Validator = Depends(get_request_validator)
) -> ValidatorDisconnectResponse:

    logger.info(f"Validator '{validator.name}' disconnected")
    logger.info(f"  Reason: {request.reason}")

    if validator.current_evaluation_id:
        logger.info(
            f"Validator '{validator.name}' marking evaluation runs as cancelled for current evaluation {validator.current_evaluation_id}..."
        )
        await mark_running_evaluation_runs_as_errored(validator.current_evaluation_id)
        logger.info(
            f"Validator '{validator.name}' marked evaluation runs as cancelled for evaluation {validator.current_evaluation_id}"
        )

        logger.debug(
            f"Validator '{validator.name}' marking evaluation as finished for evaluation {validator.current_evaluation_id}..."
        )
        await mark_evaluation_as_finished(validator.current_evaluation_id)
        logger.info(
            f"Validator '{validator.name}' marked evaluation as finished for evaluation {validator.current_evaluation_id}"
        )

    del SESSION_ID_TO_VALIDATOR[validator.session_id]

    return ValidatorDisconnectResponse()


# /validator/finish-evaluation
class ValidatorFinishEvaluationRequest(BaseModel):
    pass
class ValidatorFinishEvaluationResponse(BaseModel):
    pass

@router.post("/finish-evaluation")
async def validator_finish_evaluation(
    request: ValidatorFinishEvaluationRequest,
    validator: Validator = Depends(get_request_validator)
) -> ValidatorFinishEvaluationResponse:

    # Make sure the validator is currently running an evaluation
    if validator.current_evaluation_id is None:
        raise HTTPException(
            status_code=409,
            detail="This validator is not currently running an evaluation, and therefore cannot request to finish an evaluation."
        )

    # Make sure that all evaluation runs have either finished or errored
    evaluation_runs = await get_all_evaluation_runs_for_evaluation_id(validator.current_evaluation_id)
    if any(
        evaluation_run.status not in [EvaluationRunStatus.finished, EvaluationRunStatus.error]
        for evaluation_run in evaluation_runs
    ):
        raise HTTPException(
            status_code=409,
            detail="Not all evaluation runs associated with the evaluation that this validator is currently running have either finished or errored. Did you forget to send an update-evaluation-run?"
        )

    logger.debug(
        f"Validator '{validator.name}' marking evaluation as finished for evaluation {validator.current_evaluation_id}..."
    )
    await mark_evaluation_as_finished(validator.current_evaluation_id)
    logger.info(
        f"Validator '{validator.name}' marked evaluation as finished for evaluation {validator.current_evaluation_id}"
    )

    validator.current_evaluation_id = None

    return ValidatorFinishEvaluationResponse()



class ConnectedValidatorInfo(BaseModel):
    name: str
    hotkey: str
    time_connected: datetime

    time_last_heartbeat: Optional[datetime] = None
    system_metrics: SystemMetrics = SystemMetrics()

    evaluation: Optional[Evaluation] = None
    agent: Optional[Agent] = None

@router.get("/connected-validators-info")
async def validator_connected_validators_info() -> List[ConnectedValidatorInfo]:
    connected_validators: List[ConnectedValidatorInfo] = []

    for validator in SESSION_ID_TO_VALIDATOR.values():
        connected_validator = ConnectedValidatorInfo(
            name=validator.name,
            hotkey=validator.hotkey,
            time_connected=validator.time_connected,
            time_last_heartbeat=validator.time_last_heartbeat,
            system_metrics=validator.system_metrics
        )

        if validator.current_evaluation_id is not None:
            connected_validator.evaluation = await get_evaluation_by_id(validator.current_evaluation_id)
            connected_validator.agent = await get_agent_by_id(connected_validator.evaluation.agent_id)

        connected_validators.append(connected_validator)

    return connected_validators