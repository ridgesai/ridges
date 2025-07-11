import asyncio
from datetime import timedelta

import asyncpg

from api.src.backend.db_manager import db_operation

import logging

logger = logging.getLogger(__name__)

@db_operation
async def clean_hanging_evaluations(conn: asyncpg.Connection) -> int:
    """
    Clean up evaluations that are stuck in 'running' status.
    For each running evaluation:
    1. Set status to 'waiting'
    2. Set nullable fields to null (started_at, finished_at, score, terminated_reason)
    3. Delete all associated evaluation runs
    Returns the number of evaluations cleaned up.
    """
    cleaned_count = await conn.fetchval("""
        WITH cleaned_evaluations AS (
            UPDATE evaluations 
            SET status = 'waiting',
                started_at = NULL,
                finished_at = NULL,
                score = NULL,
                terminated_reason = NULL
            WHERE status = 'running'
            RETURNING evaluation_id
        ),
        deleted_inferences AS (
            DELETE FROM inferences 
            WHERE run_id IN (
                SELECT run_id FROM evaluation_runs 
                WHERE evaluation_id IN (SELECT evaluation_id FROM cleaned_evaluations)
            )
        ),
        deleted_embeddings AS (
            DELETE FROM embeddings 
            WHERE run_id IN (
                SELECT run_id FROM evaluation_runs 
                WHERE evaluation_id IN (SELECT evaluation_id FROM cleaned_evaluations)
            )
        ),
        deleted_runs AS (
            DELETE FROM evaluation_runs 
            WHERE evaluation_id IN (SELECT evaluation_id FROM cleaned_evaluations)
        )
        SELECT COUNT(*) as cleaned_count
        FROM cleaned_evaluations
    """)
    return cleaned_count

@db_operation
async def clean_timed_out_evaluations(conn: asyncpg.Connection) -> int:
    """
    Clean up evaluations that have been running for more than 150 minutes.
    For each timed out evaluation:
    1. Set status to 'waiting'
    2. Set nullable fields to null (started_at, finished_at, score, terminated_reason)
    3. Delete all associated evaluation runs
    Returns the number of evaluations cleaned up.
    """
    timed_out_evaluations = await conn.fetch("""
        SELECT evaluation_id 
        FROM evaluations 
        WHERE status = 'running' 
        AND started_at < NOW() - INTERVAL '150 minutes'
    """)
    
    if not timed_out_evaluations:
        logger.info("Tried to clean up timed out evaluations, but no evaluations have been running for over 150 minutes")
        return 0
    
    cleaned_count = 0
    
    async with conn.transaction():
        for row in timed_out_evaluations:
            evaluation_id = row['evaluation_id']
            
            # Delete associated runs and related records
            runs_deleted = await conn.execute("""
                WITH deleted_inferences AS (
                    DELETE FROM inferences 
                    WHERE run_id IN (
                        SELECT run_id FROM evaluation_runs WHERE evaluation_id = $1
                    )
                ),
                deleted_embeddings AS (
                    DELETE FROM embeddings 
                    WHERE run_id IN (
                        SELECT run_id FROM evaluation_runs WHERE evaluation_id = $1
                    )
                )
                DELETE FROM evaluation_runs WHERE evaluation_id = $1
            """, evaluation_id)
            
            # Reset evaluation status
            rows_updated = await conn.execute("""
                UPDATE evaluations 
                SET status = 'waiting',
                    started_at = NULL,
                    finished_at = NULL,
                    score = NULL,
                    terminated_reason = NULL
                WHERE evaluation_id = $1
            """, evaluation_id)
            
            # Extract number from status string (e.g., "DELETE 5" -> 5)
            runs_deleted_count = int(runs_deleted.split()[-1]) if runs_deleted.split()[-1].isdigit() else 0
            rows_updated_count = int(rows_updated.split()[-1]) if rows_updated.split()[-1].isdigit() else 0
            
            if rows_updated_count > 0:
                cleaned_count += 1
                logger.info(f"Cleaned up timed out evaluation {evaluation_id}: deleted {runs_deleted_count} associated runs, reset to waiting")
    
    logger.info(f"Successfully cleaned up {cleaned_count} timed out evaluations")
    return cleaned_count

async def evaluation_cleanup_loop(every: timedelta):
    logger.info(f"Starting evaluation cleanup loop - running every {every}")
    
    while True:
        try:
            await clean_timed_out_evaluations()
            logger.info(f"Evaluation cleanup completed. Running again in {every}.")
            await asyncio.sleep(every.seconds) 
        except Exception as e:
            logger.error(f"Error in evaluation cleanup loop: {e}")
            await asyncio.sleep(every.seconds)