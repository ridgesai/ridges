import asyncio
from datetime import timedelta
import logging
import asyncpg

from api.src.backend.db_manager import db_operation
from api.src.backend.queries.evaluations import get_running_evaluations, reset_evaluation

logger = logging.getLogger(__name__)


@db_operation
async def clean_running_evaluations(conn: asyncpg.Connection) -> int:
    """
    Clean up evaluations that are stuck in 'running' status.
    This happens most often when the platform is restarted, having no chance to reset
    running evaluations.
    """
    running_evaluations = await get_running_evaluations()
    for evaluation in running_evaluations:
        await reset_evaluation(evaluation.evaluation_id)
    return len(running_evaluations)


@db_operation
async def clean_timed_out_evaluations(conn: asyncpg.Connection) -> int:
    """
    Clean up evaluations that have been running for more than 150 minutes.
    This is rare, happening most when validators get deregistered.
    """

    timed_out_evaluation_ids = await conn.fetch(
        """
        SELECT evaluation_id 
        FROM evaluations 
        WHERE status = 'running' 
        AND started_at < NOW() - INTERVAL '150 minutes'
    """
    )

    if not timed_out_evaluation_ids:
        logger.info("No timed out evaluations found")
        return 0

    for evaluation_id in timed_out_evaluation_ids:
        await reset_evaluation(evaluation_id)
    return len(timed_out_evaluation_ids)


async def evaluation_timeout_cleanup_loop(every: timedelta):
    logger.info(f"Starting evaluation timeout cleanup loop - running every {every}")

    while True:
        try:
            await clean_timed_out_evaluations()
            logger.info(f"Evaluation timeout cleanup completed. Running again in {every}.")
        except Exception as e:
            logger.error(f"Error in evaluation timeout cleanup loop: {e}")
        finally:
            await asyncio.sleep(every.seconds)
