import asyncio
import re

import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from uuid import UUID, uuid4


from utils.git import COMMIT_HASH
from utils.debug_lock import DebugLock
from http import HTTPStatus
from fastapi import Depends, APIRouter, HTTPException, Request
from fastapi.security import HTTPBearer
from pydantic import BaseModel
from functools import wraps
import api.config as config
import utils.logger as logger
from queries.agent import get_top_agents, get_agent_by_id, update_agent_status, get_next_agent_id_awaiting_evaluation_for_validator_hotkey
from queries.evaluation import get_hydrated_evaluation_by_id, update_evaluation_finished_at, create_new_evaluation_and_evaluation_runs, get_num_successful_validator_evaluations_for_agent_id, update_unfinished_evaluation_runs_in_evaluation_id_to_errored
from queries.evaluation_run import get_evaluation_run_by_id, update_evaluation_run_by_id, \
    get_all_evaluation_runs_in_evaluation_id, create_evaluation_run_log, check_if_evaluation_run_logs_exist
from models.agent import Agent, AgentStatus
from models.evaluation import Evaluation, EvaluationStatus
from models.evaluation_run import EvaluationRunStatus, EvaluationRunLogType
from models.problem import ProblemTestResult
from utils.bittensor import validate_signed_timestamp
from utils.s3 import download_text_file_from_s3
from utils.system_metrics import SystemMetrics
from utils.validator_hotkeys import validator_hotkey_to_name, is_validator_hotkey_whitelisted
from api.endpoints.validator_models import *



# A validator
class Validator(BaseModel):
    session_id: UUID

    name: str
    hotkey: str
    time_connected: datetime
    ip_address: str

    current_evaluation_id: Optional[UUID] = None
    current_evaluation: Optional[Evaluation] = None
    current_agent: Optional[Agent] = None

    time_last_heartbeat: Optional[datetime] = None
    system_metrics: Optional[SystemMetrics] = None
    


    _lock: asyncio.Lock

    def __init__(self, **data):
        super().__init__(**data)
        self._lock = asyncio.Lock()



# Map of session IDs to validator objects
SESSION_ID_TO_VALIDATOR: Dict[UUID, Validator] = {}

# Returns True if a validator with the given hotkey is currently registered
def is_validator_registered(validator_hotkey: str) -> bool:
    return validator_hotkey in [validator.hotkey for validator in SESSION_ID_TO_VALIDATOR.values()]

# Returns the IP addresses of all connected screeners and validators
def get_all_connected_validator_ip_addresses() -> List[str]:
    return [validator.ip_address for validator in SESSION_ID_TO_VALIDATOR.values()]



# Deletes a validator from the SESSION_ID_TO_VALIDATOR map, and cleans up its associated state
async def delete_validator(validator: Validator, reason: str) -> None:
    logger.info(f"Deleting validator {validator.name} ({validator.hotkey})...")
    logger.info(f"  Reason: {reason}")

    if validator.current_evaluation_id:
        await update_unfinished_evaluation_runs_in_evaluation_id_to_errored(validator.current_evaluation_id, reason)
        await handle_evaluation_if_finished(validator.current_evaluation_id)
        validator.current_evaluation_id = None

    del SESSION_ID_TO_VALIDATOR[validator.session_id]

    logger.info(f"Deleted validator {validator.name} ({validator.hotkey})")

# Deletes all validators that have not sent a heartbeat in long enough
async def delete_validators_that_have_not_sent_a_heartbeat() -> None:
    logger.info("Deleting validators that have not sent a heartbeat...")

    _validators = list(SESSION_ID_TO_VALIDATOR.values())
    for validator in _validators:
        time_last_heartbeat = validator.time_last_heartbeat or validator.time_connected
        if time_last_heartbeat + timedelta(seconds=config.VALIDATOR_HEARTBEAT_TIMEOUT_SECONDS) < datetime.now():
            async with DebugLock(validator._lock, f"delete_validators_that_have_not_sent_a_heartbeat() for {validator.name}'s lock"):
                if validator.session_id in SESSION_ID_TO_VALIDATOR:
                    await delete_validator(validator, f"The validator was disconnected because it did not send a heartbeat in {config.VALIDATOR_HEARTBEAT_TIMEOUT_SECONDS} seconds.")

    logger.info("Deleted validators that have not sent a heartbeat")



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

# Exactly the same as get_request_validator, but locks the validator
# The significance of this is that specific endpoints can use this dependency to prevent race conditions
async def get_request_validator_with_lock(request: Request, validator: Validator = Depends(get_request_validator)):
    async with DebugLock(validator._lock, f"{request.scope.get("endpoint").__name__}() for {validator.name}'s lock"):
        # Make sure the session_id is still associated with a validator
        if validator.session_id not in SESSION_ID_TO_VALIDATOR:
            raise HTTPException(
                status_code=401,
                detail="Session ID not found or expired."
            )
        
        yield validator



# Catches HTTP exceptions and cleans up the associated validator
def handle_validator_http_exceptions(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except HTTPException as e:
            logger.error(f"Validator HTTP exception: {e.status_code} {e.detail}")
            await delete_validator(kwargs['validator'], f"An HTTP exception was raised in {func.__name__}(): {e.status_code} {HTTPStatus(e.status_code).phrase}: {e.detail}")
            raise
    return wrapper



router = APIRouter()



# /validator/register-as-validator
@router.post("/register-as-validator")
async def validator_register_as_validator(
    request: Request,
    registration_request: ValidatorRegistrationRequest
) -> ValidatorRegistrationResponse:

    # Ensure that the commit hash matches
    if registration_request.commit_hash != COMMIT_HASH:
        raise HTTPException(
            status_code=426,
            detail=f"The provided validator commit hash ({registration_request.commit_hash}) does not match the platform commit hash ({COMMIT_HASH}). Run `git pull` to update your validator, and try again.",
            headers={"X-Commit-Hash": COMMIT_HASH}
        )

    # Ensure that the hotkey is in the list of acceptable validator hotkeys
    if not is_validator_hotkey_whitelisted(registration_request.hotkey):
        raise HTTPException(
            status_code=403,
            detail="The provided hotkey is not in the list of whitelisted validator hotkeys."
        )
    
    # Check if the signed timestamp is valid (i.e., matches the raw timestamp)
    if not validate_signed_timestamp(registration_request.timestamp, registration_request.signed_timestamp, registration_request.hotkey):
        raise HTTPException(
            status_code=401,
            detail="The provided signed timestamp does not match the provided timestamp."
        )

    # Ensure that the timestamp is within 1 minute of the current time
    if abs(int(registration_request.timestamp) - int(time.time())) > 60:
        raise HTTPException(
            status_code=400,
            detail="The provided timestamp is not within 1 minute of the current time."
        )

    # Ensure that the validator is not already registered
    if is_validator_registered(registration_request.hotkey):
        raise HTTPException(
            status_code=409,
            detail=f"There is already a validator connected with the given hotkey."
        )

    # Register the validator with a new session ID
    session_id = uuid4()
    ip_address = request.client.host if request.client else None
    SESSION_ID_TO_VALIDATOR[session_id] = Validator(
        session_id=session_id,
        name=validator_hotkey_to_name(registration_request.hotkey),
        hotkey=registration_request.hotkey,
        time_connected=datetime.now(),
        ip_address=ip_address
    )
    
    logger.info(f"Validator '{validator_hotkey_to_name(registration_request.hotkey)}' ({registration_request.hotkey}) was registered")
    logger.info(f"  Session ID: {session_id}")
    logger.info(f"  IP Address: {ip_address}")
    
    return ValidatorRegistrationResponse(
        session_id=session_id,
        running_agent_timeout_seconds=config.VALIDATOR_RUNNING_AGENT_TIMEOUT_SECONDS,
        running_eval_timeout_seconds=config.VALIDATOR_RUNNING_EVAL_TIMEOUT_SECONDS,
        max_evaluation_run_log_size_bytes=config.VALIDATOR_MAX_EVALUATION_RUN_LOG_SIZE_BYTES
    )



# /validator/register-as-screener
@router.post("/register-as-screener")
async def validator_register_as_screener(
    request: Request,
    registration_request: ScreenerRegistrationRequest
) -> ScreenerRegistrationResponse:

    # Ensure that the commit hash matches
    if registration_request.commit_hash != COMMIT_HASH:
        raise HTTPException(
            status_code=426,
            detail=f"The provided screener commit hash ({registration_request.commit_hash}) does not match the platform commit hash ({COMMIT_HASH}). Run `git pull` to update your screener, and try again.",
            headers={"X-Commit-Hash": COMMIT_HASH}
        )

    # Ensure that the name is in the format screener-CLASS-NUM
    if not re.match(r"screener-\d-\d+", registration_request.name):
        raise HTTPException(
            status_code=400,
            detail="The provided name is not in the format screener-CLASS-NUM."
        )
    
    # Ensure that the CLASS is either 1 or 2
    screener_class = registration_request.name.split("-")[1]
    if screener_class != "1" and screener_class != "2":
        raise HTTPException(
            status_code=400,
            detail="The screener class must be either 1 or 2."
        )
    
    # Ensure that the password is correct
    if registration_request.password != config.SCREENER_PASSWORD:
        raise HTTPException(
            status_code=403,
            detail="The provided password is incorrect."
        )

    # Ensure that the screener is not already registered
    if is_validator_registered(registration_request.name):
        raise HTTPException(
            status_code=409,
            detail=f"There is already a screener connected with the given name."
        )

    # Register the screener with a new session ID
    session_id = uuid4()
    ip_address = request.client.host if request.client else None
    SESSION_ID_TO_VALIDATOR[session_id] = Validator(
        session_id=session_id,
        name=registration_request.name,
        hotkey=registration_request.name,
        time_connected=datetime.now(),
        ip_address=ip_address
    )
    
    logger.info(f"Screener {registration_request.name} was registered")
    logger.info(f"  Session ID: {session_id}")
    logger.info(f"  IP Address: {ip_address}")
    
    return ScreenerRegistrationResponse(
        session_id=session_id,
        running_agent_timeout_seconds=config.VALIDATOR_RUNNING_AGENT_TIMEOUT_SECONDS,
        running_eval_timeout_seconds=config.VALIDATOR_RUNNING_EVAL_TIMEOUT_SECONDS,
        max_evaluation_run_log_size_bytes=config.VALIDATOR_MAX_EVALUATION_RUN_LOG_SIZE_BYTES
    )



# /validator/request-evaluation
validator_request_evaluation_lock = asyncio.Lock()
screener_1_request_evaluation_lock = asyncio.Lock()
screener_2_request_evaluation_lock = asyncio.Lock()

@router.post("/request-evaluation")
@handle_validator_http_exceptions
async def validator_request_evaluation(
    request: ValidatorRequestEvaluationRequest,
    validator: Validator = Depends(get_request_validator_with_lock)
) -> Optional[ValidatorRequestEvaluationResponse]:

    # Make sure the validator is not already running an evaluation
    if validator.current_evaluation_id is not None:
        raise HTTPException(
            status_code=409,
            detail=f"This validator is already running an evaluation, and validators may only run one evaluation at a time."
        )

    # Choose the appropriate lock based on the validator's hotkey
    if validator.hotkey.startswith("screener-1"):
        lock = screener_1_request_evaluation_lock
        lock_name = "screener_1_request_evaluation_lock"
    elif validator.hotkey.startswith("screener-2"):
        lock = screener_2_request_evaluation_lock
        lock_name = "screener_2_request_evaluation_lock"
    else:
        lock = validator_request_evaluation_lock
        lock_name = "validator_request_evaluation_lock"

    # Try to acquire the lock, but don't hang forever
    # TODO: .env
    try:
        async with DebugLock(lock, f"{validator.name} ({validator.hotkey}) for {lock_name}", timeout=30):
            # Find the next agent awaiting an evaluation from this validator
            agent_id = await get_next_agent_id_awaiting_evaluation_for_validator_hotkey(validator.hotkey)
            if agent_id is None:
                return None

            # Create a new evaluation and evaluation runs for this agent & validator
            evaluation, evaluation_runs = await create_new_evaluation_and_evaluation_runs(agent_id, validator.hotkey)
    except asyncio.TimeoutError:
        # We couldn't acquire the lock, just act as though there are no evaluations available
        # The validator will automatically retry in a few seconds
        # This should prevent a ridiculous amount of lock contention
        return None

    validator.current_evaluation_id = evaluation.evaluation_id
    validator.current_evaluation = evaluation
    validator.current_agent = await get_agent_by_id(agent_id)



    logger.info(f"Validator '{validator.name}' requested an evaluation")
    logger.info(f"  Agent ID: {agent_id}")
    logger.info(f"  Evaluation ID: {evaluation.evaluation_id}")
    logger.info(f"  # of Evaluation Runs: {len(evaluation_runs)}")

    agent_code = await download_text_file_from_s3(f"{agent_id}/agent.py")
    evaluation_runs = [ValidatorRequestEvaluationResponseEvaluationRun(evaluation_run_id=evaluation_run.evaluation_run_id, problem_name=evaluation_run.problem_name) for evaluation_run in evaluation_runs]

    return ValidatorRequestEvaluationResponse(agent_code=agent_code, evaluation_runs=evaluation_runs)



# /validator/heartbeat
@router.post("/heartbeat")
async def validator_heartbeat(
    request: ValidatorHeartbeatRequest,
    validator: Validator = Depends(get_request_validator) # No lock required
) -> ValidatorHeartbeatResponse:

    # logger.info(f"Validator '{validator.name}' sent a heartbeat")
    # logger.info(f"  System metrics: {request.system_metrics}")

    validator.time_last_heartbeat = datetime.now()
    validator.system_metrics = request.system_metrics
    
    return ValidatorHeartbeatResponse()



# /validator/update-evaluation-run
@router.post("/update-evaluation-run")
@handle_validator_http_exceptions
async def validator_update_evaluation_run(
    request: ValidatorUpdateEvaluationRunRequest,
    validator: Validator = Depends(get_request_validator_with_lock)
) -> ValidatorUpdateEvaluationRunResponse:
    
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

            # Create an evaluation run log
            await create_evaluation_run_log(evaluation_run.evaluation_run_id, EvaluationRunLogType.agent, request.agent_logs)

    

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
            evaluation_run.test_results = request.test_results
            evaluation_run.finished_or_errored_at = datetime.now()

            # Create an evaluation run log
            await create_evaluation_run_log(evaluation_run.evaluation_run_id, EvaluationRunLogType.eval, request.eval_logs)
    


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

            # Agent logs can only be provided if none already exist
            if request.agent_logs is not None and await check_if_evaluation_run_logs_exist(evaluation_run.evaluation_run_id, EvaluationRunLogType.agent):
                raise HTTPException(
                    status_code=422,
                    detail="The agent logs can only be provided if none already exist."
                )

            # Evaluation logs can only be provided if none already exist
            if request.eval_logs is not None and await check_if_evaluation_run_logs_exist(evaluation_run.evaluation_run_id, EvaluationRunLogType.eval):
                raise HTTPException(
                    status_code=422,
                    detail="The eval logs can only be provided if none already exist."
                )

            # Update the evaluation run to error
            evaluation_run.status = EvaluationRunStatus.error
            evaluation_run.error_code = request.error_code
            evaluation_run.error_message = request.error_message
            evaluation_run.finished_or_errored_at = datetime.now()

            # Create evaluation run logs
            if request.agent_logs is not None:
                await create_evaluation_run_log(evaluation_run.evaluation_run_id, EvaluationRunLogType.agent, request.agent_logs)
            if request.eval_logs is not None:
                await create_evaluation_run_log(evaluation_run.evaluation_run_id, EvaluationRunLogType.eval, request.eval_logs)



    await update_evaluation_run_by_id(evaluation_run)

    logger.info(f"Validator '{validator.name}' updated an evaluation run")
    logger.info(f"  Evaluation run ID: {request.evaluation_run_id}")
    logger.info(f"  Updated status: {request.updated_status}")

    return ValidatorUpdateEvaluationRunResponse()



# /validator/disconnect
@router.post("/disconnect")
async def validator_disconnect(
    request: ValidatorDisconnectRequest,
    validator: Validator = Depends(get_request_validator_with_lock)
) -> ValidatorDisconnectResponse:

    await delete_validator(validator, f"The validator disconnected. Reason: {request.reason}")

    logger.info(f"Validator '{validator.name}' disconnected")
    logger.info(f"  Reason: {request.reason}")

    return ValidatorDisconnectResponse()



# /validator/finish-evaluation
@router.post("/finish-evaluation")
@handle_validator_http_exceptions
async def validator_finish_evaluation(
    request: ValidatorFinishEvaluationRequest,
    validator: Validator = Depends(get_request_validator_with_lock)
) -> ValidatorFinishEvaluationResponse:

    # Make sure the validator is currently running an evaluation
    if validator.current_evaluation_id is None:
        raise HTTPException(
            status_code=409,
            detail="This validator is not currently running an evaluation, and therefore cannot request to finish an evaluation."
        )

    # Make sure that all evaluation runs have either finished or errored
    evaluation_runs = await get_all_evaluation_runs_in_evaluation_id(validator.current_evaluation_id)
    if any(evaluation_run.status not in [EvaluationRunStatus.finished, EvaluationRunStatus.error] for evaluation_run in evaluation_runs):
        raise HTTPException(
            status_code=409,
            detail="Not all evaluation runs associated with the evaluation that this validator is currently running have either finished or errored. Did you forget to send an update-evaluation-run?"
        )



    await handle_evaluation_if_finished(validator.current_evaluation_id)

    logger.info(f"Validator '{validator.name}' finished an evaluation")
    logger.info(f"  Evaluation ID: {validator.current_evaluation_id}")

    validator.current_evaluation_id = None

    return ValidatorFinishEvaluationResponse()









# /validator/connected-validators-info
@router.get("/connected-validators-info")
async def validator_connected_validators_info() -> List[ConnectedValidatorInfo]:
    connected_validators: List[ConnectedValidatorInfo] = []

    _validators = list(SESSION_ID_TO_VALIDATOR.values())
    for validator in _validators:
        connected_validator = ConnectedValidatorInfo(
            name=validator.name,
            hotkey=validator.hotkey,
            time_connected=validator.time_connected,
            time_last_heartbeat=validator.time_last_heartbeat,
            system_metrics=validator.system_metrics,
        )

        if validator.current_evaluation_id is not None:
            connected_validator.evaluation = validator.current_evaluation
            connected_validator.agent = validator.current_agent

        connected_validators.append(connected_validator)

    return connected_validators






async def handle_evaluation_if_finished(evaluation_id: UUID) -> None:
    """
    Adds the finished_at field to an evaluation. If the evaluation is marked as successful
    (all evaluation runs completed successfully), transitions the corresponding agent into the next state.
    """
    await update_evaluation_finished_at(evaluation_id)

    hydrated_evaluation = await get_hydrated_evaluation_by_id(evaluation_id)

    # Transition agent state if this evaluation was successful
    if hydrated_evaluation.status == EvaluationStatus.success:
        agent = await get_agent_by_id(hydrated_evaluation.agent_id)
        new_agent_status = None

        match agent.status:
            case AgentStatus.screening_1:
                if hydrated_evaluation.score >= config.SCREENER_1_THRESHOLD:
                    new_agent_status = AgentStatus.screening_2
                else:
                    new_agent_status = AgentStatus.failed_screening_1

            case AgentStatus.screening_2:
                top_agents = await get_top_agents(number_of_agents=1)
                top_score = top_agents[0].final_score if top_agents else 0
                pruning_threshold_score = top_score * config.PRUNE_THRESHOLD

                if hydrated_evaluation.score >= max(config.SCREENER_2_THRESHOLD, pruning_threshold_score):
                    new_agent_status = AgentStatus.evaluating
                else:
                    new_agent_status = AgentStatus.failed_screening_2

            case AgentStatus.evaluating:
                num_validator_evaluations = await get_num_successful_validator_evaluations_for_agent_id(agent.agent_id)
                if num_validator_evaluations >= config.NUM_EVALS_PER_AGENT:
                    new_agent_status = AgentStatus.finished
                else:
                    new_agent_status = AgentStatus.evaluating

            case _:
                # TODO ADAM: this could actually happen if someone manually messed with the database. we need to handle this better
                # raise ValueError(f"Invalid agent status: {agent.status}, this should never happen")
                return

        await update_agent_status(hydrated_evaluation.agent_id, new_agent_status)