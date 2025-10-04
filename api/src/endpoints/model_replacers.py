"""
TEMPORARY FILE - use to put logic that we need in the models folder that don't have a clear endpoints file to go into
"""

from api.src.backend.entities import AgentStatus, MinerAgent
from api.src.backend.queries.agents import get_top_agent, set_agent_status
from api.src.backend.queries.evaluations import get_running_evaluations, get_stuck_evaluations, get_waiting_evaluations, cancel_dangling_evaluation_runs, reset_evaluation_to_waiting, update_evaluation_to_error
from api.src.backend.queries.agents import agent_startup_recovery
from api.src.endpoints.screener import atomically_update_agent_status, finish_evaluation, prune_queue
from loggers.logging_utils import get_logger

logger = get_logger(__name__)


async def repair_agent_status():
    """Handles:
        - Screener disconnects
        - Validator disconnects
        - Platform restarts
    """
    pass

async def replace_old_agents(agent: MinerAgent):
    pass

async def update_agent_status(agent: MinerAgent):
    """Update agent status based on evaluation state - handles multi-stage screening"""
    
    # We use the database as the source of truth now. Fetch evaluations and then use that to determine how to update agent status

    return

@staticmethod
async def startup_recovery():
    """Fix broken states from shutdown - handles multi-stage screening"""
    await agent_startup_recovery()

    # Reset running evaluations
    running_evals = await get_running_evaluations()
    for eval_row in running_evals:
        evaluation_id = eval_row.evaluation_id
        agent_version_id = eval_row.version_id
        from api.src.models.screener import Screener
        is_screening = Screener.get_stage(eval_row.validator_hotkey) is not None
        if is_screening:
            await update_evaluation_to_error(evaluation_id, "Disconnected from screener (error code 2)")
            await atomically_update_agent_status(version_id=agent_version_id)
        else:
            # set evaluation to waiting, and its runs to cancelled
            await reset_evaluation_to_waiting(evaluation_id)
            # set agent status to waiting
            await set_agent_status(
                version_id=agent_version_id,
                status=AgentStatus.waiting.value
            )

    # Check for running evaluations that should be auto-completed
    stuck_evaluations = await get_stuck_evaluations()

    for stuck_eval in stuck_evaluations:
        evaluation_id = stuck_eval.evaluation_id
        # evaluation = await get_evaluation_by_evaluation_id(evaluation_id)
        validator_hotkey = stuck_eval.validator_hotkey

        logger.info(f"Auto-completing stuck evaluation {evaluation_id} during startup recovery")
        # During startup recovery, don't trigger notifications
        _ = await finish_evaluation(evaluation_id, validator_hotkey, errored=True, reason="Platform restarted")

    # Cancel waiting screenings for all screener types
    waiting_screenings = await get_waiting_evaluations()
    for screening_row in waiting_screenings:
        evaluation_id = screening_row.evaluation_id
        evaluation_version_id = screening_row.version_id

        # await evaluation.error("Disconnected from screener (error code 3)")
        await update_evaluation_to_error(evaluation_id, "Disconnected from screener (error code 3)")
        await atomically_update_agent_status(version_id=evaluation_version_id)

    # Cancel dangling evaluation runs
    await cancel_dangling_evaluation_runs()

    # Prune low-scoring evaluations that should not continue waiting
    top_agent = await get_top_agent()
    if top_agent:
        await prune_queue(top_agent)

    logger.info("Application startup recovery completed with multi-stage screening support")

