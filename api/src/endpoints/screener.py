"""
All logic around screeners, including starting a screening, finishing it, handling state updates, etc
"""

import asyncio
import uuid
from typing import Optional
from api.src.backend.entities import AgentStatus
from logging import getLogger

from api.src.backend.queries.agents import get_top_agent, set_agent_status
from api.src.backend.queries.evaluations import create_evaluation, get_evaluation_by_evaluation_id, get_evaluation_for_version_validator_and_set, get_inference_success_rate, prune_evaluations_in_queue, reset_evaluation_to_waiting, update_evaluation_to_completed, update_evaluation_to_error
from api.src.backend.queries.scores import get_combined_screener_score, get_current_set_id
from api.src.endpoints.agents import get_agent_by_version

from api.src.utils.config import PRUNE_THRESHOLD, SCREENING_1_THRESHOLD, SCREENING_2_THRESHOLD
from api.src.utils.models import TopAgentHotkey

logger = getLogger(__name__)

SCREENING_STATUSES = [AgentStatus.screening_1.value, AgentStatus.screening_2.value]

async def start_screening():
    pass

async def finish_screening(
    evaluation_id: str,
    screener_hotkey: str,
    errored: bool = False,
    reason: Optional[str] = None
):
    evaluation = await get_evaluation_by_evaluation_id(evaluation_id)

    if not evaluation or evaluation.status not in SCREENING_STATUSES or evaluation.validator_hotkey != screener_hotkey:
        logger.warning(f"Screener {screener_hotkey}: Invalid finish_screening call for evaluation {evaluation_id}")
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
                status=AgentStatus.awaiting_screening_1.value if evaluation.status == "screening_1" else AgentStatus.awaiting_screening_2.value
            )
        )
        
        logger.info(f"Screener {screener_hotkey}: Finishing screening {evaluation_id}: Errored with reason: {reason}")
    
    # Check inference success rate. If errored, set the screening back to awaiting and update this evaluation with errored 
    successful, total, success_rate, any_run_errored = await get_inference_success_rate(evaluation_id=evaluation_id)

    if total > 0 and success_rate < 0.5 and any_run_errored:
        await reset_evaluation_to_waiting(evaluation_id)
        # Set the agent back to awaiting for the same screener level if errored
        await set_agent_status(
            version_id=agent.version_id,
            status=AgentStatus.awaiting_screening_1.value if evaluation.status == "screening_1" else AgentStatus.awaiting_screening_2.value
        )
        return

    await update_evaluation_to_completed(evaluation_id=evaluation_id)

    # Check whether it passed the screening thresholds.
    threshold = SCREENING_1_THRESHOLD if evaluation.status == "screening_1" else SCREENING_2_THRESHOLD

    if evaluation.score < threshold:
        # Agent has failed, update status and that's that
        await set_agent_status(
            version_id=agent.version_id,
            status=AgentStatus.failed_screening_1.value if evaluation.status == "screening_1" else AgentStatus.failed_screening_2.value
        )

        return

    if evaluation.status == AgentStatus.screening_1.value:
        await set_agent_status(
            version_id=agent.version_id,
            status=AgentStatus.awaiting_screening_2.value
        )

        return

    if evaluation.status == AgentStatus.screening_2.value:
        # If screening 2, see if we should prune it if its behind the top agent by enough, and create validator evals if not
        combined_screener_score, score_error = await get_combined_screener_score(agent.version_id)
        top_agent = await get_top_agent()

        if top_agent and combined_screener_score is not None and (top_agent.avg_score - combined_screener_score) > PRUNE_THRESHOLD:
            # Score is too low, prune miner agent and don't create evaluations
            await set_agent_status(
                version_id=agent.version_id,
                status=AgentStatus.pruned
            )

            await prune_queue(top_agent)
            
            return

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
        await prune_queue(top_agent)
        
        return

    logger.error(f"Invalid screener status {evaluation.status}")

async def create_evaluation_for_validator(version_id: str, validator_hotkey: str, combined_screener_score: float) -> str:
    max_set_id = await get_current_set_id()

    existing_evaluation_id = get_evaluation_for_version_validator_and_set(
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

    prune_evaluations_in_queue(threshold, max_set_id)

async def handle_disconnect():
    pass
