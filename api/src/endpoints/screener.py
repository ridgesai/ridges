"""
All logic around screeners, including starting a screening, finishing it, handling state updates, etc
"""

import asyncio
from datetime import datetime, timezone
import stat
from sys import version
import uuid
from typing import Any, Optional

from fastapi import status
from slack_bolt.context import complete
from api.src.backend.entities import AgentStatus, EvaluationRun, EvaluationStatus, MinerAgent, SandboxStatus
from logging import getLogger

from api.src.backend.queries.agents import get_top_agent, set_agent_status
from api.src.backend.queries.evaluations import check_for_currently_running_eval, create_evaluation, create_evaluation_runs, evaluation_count_for_agent_and_status, get_evaluation_by_evaluation_id, get_evaluation_for_version_validator_and_set, get_inference_success_rate, get_problems_for_set_and_stage, prune_evaluations_in_queue, reset_evaluation_to_waiting, update_evaluation_to_completed, update_evaluation_to_error, update_evaluation_to_started
from api.src.backend.queries.scores import get_combined_screener_score, get_current_set_id, update_innovation_score
from api.src.endpoints.agents import get_agent_by_version

from api.src.models.screener import Screener
from api.src.socket.websocket_manager import WebSocketManager
from api.src.utils.config import PRUNE_THRESHOLD, SCREENING_1_THRESHOLD, SCREENING_2_THRESHOLD
from api.src.utils.models import TopAgentHotkey

logger = getLogger(__name__)

AWAITING_SCREENING_STATUSES = [AgentStatus.screening_1.value, AgentStatus.screening_2.value]
SCREENING_STATUSES = [AgentStatus.screening_1.value, AgentStatus.screening_2.value]

from enum import Enum
class ValidationStage(Enum):
    SCREENER_1 = "screener-1"
    SCREENER_2 = "screener-2"
    VALIDATION = "validator"

def identify_validation_stage(hotkey: str) -> ValidationStage:
    if "screener-1" in hotkey:
        return ValidationStage.SCREENER_1
    elif "screener-2" in hotkey:
        return ValidationStage.SCREENER_2
    else:
        # TODO: Verify sn58 format 
        return ValidationStage.VALIDATION

def match_validation_stage_to_agent_status(validation_stage: ValidationStage) -> AgentStatus:
    if validation_stage == ValidationStage.SCREENER_1:
        return AgentStatus.screening_1
    elif validation_stage == ValidationStage.SCREENER_2:
        return AgentStatus.screening_2
    elif validation_stage == ValidationStage.VALIDATION:
        return AgentStatus.evaluating

async def start_screening(evaluation_id: str, hotkey: str) -> dict[str, Any]:
    f"""
    Temporarily returns a dict in format:
     success: bool
     runs_created: list[EvaluationRun]
    """
    # TODO: Where is the eval inserted?
    # Get the evaluation, makes sure its screening and its the right hotkey making the request
    validation_stage = identify_validation_stage(hotkey)
    evaluation = await get_evaluation_by_evaluation_id(evaluation_id=evaluation_id)

    if not evaluation or validation_stage != identify_validation_stage(evaluation.validator_hotkey) or evaluation.validator_hotkey != hotkey:
        print(f"FAIL1. Failed to create evaluation runs. Evaluation: {evaluation}, validation stage: {validation_stage}, other validation stage: {identify_validation_stage(evaluation.validator_hotkey)}")
        return {
            "success": False,
            "runs_created": []
        }

    # Get the agent version, make sure thats in screening too
    agent = await get_agent_by_version(evaluation.version_id)

    # TODO: in old version this is set to screening by this point. Why? When allocated to screeners? Should be set here
    if not agent or agent.status != match_validation_stage_to_agent_status(validation_stage):
        print(f"FAIL2. Failed to create evaluation runs. agent: {agent}, matched vali stage: {match_validation_stage_to_agent_status(validation_stage)}")
        # For some reason only screeners set the agent state before, and so validator stuck on waiting
        if agent.status != "waiting":
            logger.error(f"Tried to start agent {evaluation.version_id} validation but either agent doesn't exist or invalid status; {agent.status if agent else 'No agent'}")
            return {
                "success": False,
                "runs_created": []
            }

    # Once checks are in place, start the evaluation
    await update_evaluation_to_started(evaluation_id)

    # Get max set ids and the problem instance ids associated
    try:
        current_set_id = await get_current_set_id()
        problem_instance_ids = await get_problems_for_set_and_stage(set_id=current_set_id, validation_stage=validation_stage.value)

        # Create eval runs and insert 
        evaluation_runs = [
            EvaluationRun(
                run_id = uuid.uuid4(),
                evaluation_id = evaluation_id,
                swebench_instance_id = problem_id,
                response=None,
                error=None,
                pass_to_fail_success=None,
                fail_to_pass_success=None,
                pass_to_pass_success=None,
                fail_to_fail_success=None,
                solved=None,
                status = SandboxStatus.started,
                started_at = datetime.now(timezone.utc),
                sandbox_created_at=None,
                patch_generated_at=None,
                eval_started_at=None,
                result_scored_at=None,
                cancelled_at=None,
            )
            for problem_id in problem_instance_ids
        ]

        # Insert eval runs
        await create_evaluation_runs(evaluation_runs=evaluation_runs)

        # Update agent status
        status = match_validation_stage_to_agent_status(validation_stage)
        await set_agent_status(
            version_id=agent.version_id, 
            status=status.value
        )

        # TODO: Broadcast status change?
        return {
            "success": True,
            "runs_created": evaluation_runs
        }
    except Exception as e:
        logger.error(f"Error starting evaluation: {e}")
        return {
            "success": False,
            "runs_created": []
        }

async def finish_screening(
    evaluation_id: str,
    hotkey: str,
    errored: bool = False,
    reason: Optional[str] = None
):
    evaluation = await get_evaluation_by_evaluation_id(evaluation_id)

    if not evaluation or evaluation.validator_hotkey != hotkey:
        logger.warning(f"Screener {hotkey}: Invalid finish_screening call for evaluation {evaluation_id}")
        return

    agent = await get_agent_by_version(evaluation.version_id)

    if agent.status not in SCREENING_STATUSES:
        logger.warning(f"Invalid status for miner agent: expected {evaluation.status}, agent is set to {agent.status}")

    if errored:
        """Error evaluation and reset agent"""
        await asyncio.gather(
            update_evaluation_to_error(evaluation_id, reason),
            set_agent_status(
                version_id=agent.version_id,
                status=AgentStatus.awaiting_screening_1.value if agent.status == "screening_1" else AgentStatus.awaiting_screening_2.value
            )
        )
        
        logger.info(f"{hotkey}: Finishing screening {evaluation_id}: Errored with reason: {reason}")
    
    # Check inference success rate. If errored, set the screening back to awaiting and update this evaluation with errored 
    _, total, success_rate, any_run_errored = await get_inference_success_rate(evaluation_id=evaluation_id)

    if total > 0 and success_rate < 0.5 and any_run_errored:
        await reset_evaluation_to_waiting(evaluation_id)
        # Set the agent back to awaiting for the same screener level if errored
        await set_agent_status(
            version_id=agent.version_id,
            status=AgentStatus.awaiting_screening_1.value if agent.status == "screening_1" else AgentStatus.awaiting_screening_2.value
        )
        return

    await update_evaluation_to_completed(evaluation_id=evaluation_id)

    # Check whether it passed the screening thresholds.
    threshold = SCREENING_1_THRESHOLD if agent.status == "screening_1" else SCREENING_2_THRESHOLD

    if evaluation.score < threshold:
        # Agent has failed, update status and that's that
        await set_agent_status(
            version_id=agent.version_id,
            status=AgentStatus.failed_screening_1.value if agent.status == "screening_1" else AgentStatus.failed_screening_2.value
        )

        return

    if agent.status == AgentStatus.screening_1.value:
        await set_agent_status(
            version_id=agent.version_id,
            status=AgentStatus.awaiting_screening_2.value
        )

        return

    if agent.status == AgentStatus.screening_2.value:
        # If screening 2, see if we should prune it if its behind the top agent by enough, and create validator evals if not
        combined_screener_score, score_error = await get_combined_screener_score(agent.version_id)
        top_agent = await get_top_agent()

        if top_agent and combined_screener_score is not None and (top_agent.avg_score - combined_screener_score) > PRUNE_THRESHOLD:
            # Score is too low, prune miner agent and don't create evaluations
            await set_agent_status(
                version_id=agent.version_id,
                status=AgentStatus.pruned.value
            )

            await prune_queue(top_agent)
            
            return

        await set_agent_status(
            version_id=agent.version_id,
            status=AgentStatus.waiting.value
        )

        # Create validator evals
        # TODO: ADAM, replace with new connected valis map
        from api.src.models.validator import Validator
        all_validators = await Validator.get_connected()

        for validator in all_validators:
            await create_evaluation_for_validator(
                version_id=agent.version_id,
                validator_hotkey=validator.hotkey,
                combined_screener_score=combined_screener_score
            )
        
        # Prune the rest of the queue
        if top_agent: 
            await prune_queue(top_agent)
        
        return

    logger.error(f"Invalid screener status {agent.status}")

# TODO
async def create_screener_evaluation(hotkey: str, agent: MinerAgent, screener: 'Screener'):
    existing_evaluation = await check_for_currently_running_eval(hotkey)

    if existing_evaluation:
        logger.error(f"CRITICAL: Screener {hotkey} already has running evaluation {existing_evaluation['evaluation_id']} - refusing to create duplicate screening")
        return False

    ws = WebSocketManager.get_instance()
    set_id = await get_current_set_id()
    evaluation_id = str(uuid.uuid4())

    await create_evaluation(
        evaluation_id=evaluation_id,
        version_id=agent.version_id,
        validator_hotkey=hotkey,
        set_id=set_id
    )

    evaluation_runs = await start_screening(evaluation_id, hotkey)

    message = {
        "event": "screen-agent",
        "evaluation_id": evaluation_id,
        "agent_version": agent.model_dump(mode="json"),
        "evaluation_runs": [run.model_dump(mode="json") for run in evaluation_runs["runs_created"]],
    }
    logger.info(f"Sending screen-agent message to screener {hotkey}: evaluation_id={evaluation_id}, agent={agent.agent_name}")
    
    await ws.send_to_all_non_validators("evaluation-started", message)
    await ws.send_to_client(screener, message)

async def create_evaluation_for_validator(version_id: str, validator_hotkey: str, combined_screener_score: float) -> str:
    max_set_id = await get_current_set_id()

    existing_evaluation_id = await get_evaluation_for_version_validator_and_set(
        version_id=version_id,
        validator_hotkey=validator_hotkey,
        set_id=max_set_id
    )

    if existing_evaluation_id:
        logger.debug(f"Evaluation already exists for version {version_id}, validator {validator_hotkey}, set {max_set_id}")
        return str(existing_evaluation_id)

    # Create new evaluation
    evaluation_id = str(uuid.uuid4())
    await create_evaluation(
        evaluation_id=evaluation_id,
        version_id=version_id,
        validator_hotkey=validator_hotkey,
        set_id=max_set_id,
        screener_score=combined_screener_score
    )
    return evaluation_id


async def prune_queue(top_agent: TopAgentHotkey):
    """
    Looks through the queue and prunes agents too far behind top agent
    """
    # Calculate the threshold (configurable lower-than-top final validation score)
    threshold = top_agent.avg_score - PRUNE_THRESHOLD
    max_set_id = await get_current_set_id()

    await prune_evaluations_in_queue(threshold, max_set_id)

async def handle_disconnect():
    pass

async def atomically_update_agent_status(version_id: str):
    """
    To be called by validators, this looks at other evaluations in the database in order to update a miner agents state
    """
    # Get the number of waiting, running, and completed/pruned evals 
    waiting_count, running_count, completed_count = await asyncio.gather(
        evaluation_count_for_agent_and_status(version_id = version_id, status = EvaluationStatus.waiting),
        evaluation_count_for_agent_and_status(version_id = version_id, status = EvaluationStatus.running),
        evaluation_count_for_agent_and_status(version_id = version_id, status = EvaluationStatus.completed),
    )

    # Use that to compute the state for miner_agent
    status_to_set: AgentStatus

    if waiting_count > 0 and running_count == 0:
        status_to_set = AgentStatus.waiting
    elif waiting_count == 0 and running_count == 0 and completed_count > 0:
        # Update innovation score before setting to scored
        await update_innovation_score(version_id=version_id)
        status_to_set = AgentStatus.scored
    else:
        status_to_set = AgentStatus.evaluating
    
    await set_agent_status(
        version_id=version_id,
        status=status_to_set.value
    )

    return 

async def finish_evaluation(
    evaluation_id: str,
    hotkey: str,
    errored: bool = False,
    reason: Optional[str] = None
):
    evaluation = await get_evaluation_by_evaluation_id(evaluation_id=evaluation_id)

    if not evaluation or evaluation.validator_hotkey != hotkey:
        logger.warning(f"Validator {hotkey}: Invalid finish_evaluation call for evaluation {evaluation_id}. {'No such eval' if evaluation is None else f'Invalid hotkey {hotkey}'}")
        return 

    # Get the agent and make sure the status is evaluating 
    agent = await get_agent_by_version(evaluation.version_id)

    if agent.status != AgentStatus.evaluating.value:
        logger.warning(f"Invalid status for miner agent: expected evaluating, agent is set to {agent.status}")

    if errored:
        """Error evaluation and reset agent"""
        await update_evaluation_to_error(evaluation_id, reason)
        await atomically_update_agent_status(version_id=evaluation.version_id)
        
        logger.info(f"{hotkey}: Finishing screening {evaluation_id}: Errored with reason: {reason}")

    # Check inference success rate. If errored, set the screening back to awaiting and update this evaluation with errored 
    _, total, success_rate, any_run_errored = await get_inference_success_rate(evaluation_id=evaluation_id)

    if total > 0 and success_rate < 0.5 and any_run_errored:
        await reset_evaluation_to_waiting(evaluation_id)
        # Set the agent back to awaiting for the same screener level if errored
        await atomically_update_agent_status(version_id=evaluation.version_id)
        return
    
    # Update evaluation to complete, and then agent status
    # We call these seperately because the agent status looks at db after this write to consider other evaluations
    await update_evaluation_to_completed(evaluation_id=evaluation_id)
    await atomically_update_agent_status(version_id=evaluation.version_id)