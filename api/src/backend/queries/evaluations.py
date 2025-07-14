from typing import Optional, List
import logging
from uuid import UUID
import uuid

import asyncpg
from datetime import datetime, timezone

from api.src.backend.db_manager import db_operation
from api.src.backend.entities import Evaluation, EvaluationRun, EvaluationsWithHydratedRuns

logger = logging.getLogger(__name__)

@db_operation
async def create_evaluation(conn: asyncpg.Connection, version_id: str, validator_hotkey: str) -> Optional[Evaluation]:
    logger.debug(f"Attempting to create evaluation for version {version_id} and validator {validator_hotkey}.")

    # Check if running evaluation exists for this validator
    running_evaluation = await get_running_evaluation_by_validator_hotkey(conn, validator_hotkey)
    if running_evaluation:
        logger.debug(f"Running evaluation already exists for validator {validator_hotkey}. Skipping creation of evaluation.")
        return None
    
    # Set waiting evaluations for this miner_hotkey and validator to replaced
    await conn.execute("""
        UPDATE evaluations 
        SET status = 'replaced', finished_at = NOW()
        WHERE validator_hotkey = $1 
        AND status = 'waiting' 
        AND version_id IN (
            SELECT ma.version_id 
            FROM miner_agents ma 
            WHERE ma.miner_hotkey = (
                SELECT miner_hotkey 
                FROM miner_agents 
                WHERE version_id = $2
            )
        )
    """, validator_hotkey, version_id)
    
    evaluation = await conn.execute("""
        INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, status, created_at)
        VALUES ($1, $2, $3, $4, $5)
    """, uuid.uuid4(), version_id, validator_hotkey, 'waiting', datetime.now(timezone.utc))

    logger.debug(f"Successfully created evaluation for version {version_id} and validator {validator_hotkey}.")

    return Evaluation(**dict(evaluation))

@db_operation
async def create_evaluations_for_validator(conn: asyncpg.Connection, validator_hotkey: str) -> int:
    logger.debug(f"Beginning to create evaluations for validator {validator_hotkey}.")

    logger.debug(f"Fetching recent agent versions from database for validator {validator_hotkey}.")
    agents = await conn.fetch(
        "SELECT ma.version_id "
        "FROM miner_agents ma "
        "WHERE ma.created_at >= NOW() - INTERVAL '24 hours' "
        "AND ma.version_num = (SELECT MAX(ma2.version_num) FROM miner_agents ma2 WHERE ma2.miner_hotkey = ma.miner_hotkey) "
        "AND ma.status = 'evaluating' "
        "ORDER BY ma.created_at DESC"
    )
    logger.debug(f"Fetched {len(agents)} recent agent versions from database for validator {validator_hotkey}.")

    if not agents:
        logger.warning(f"Tried to create evaluations for validator {validator_hotkey} but no recent agent versions found.")
        raise Exception("No recent agent versions found")
    
    evaluations_created = 0 
    import uuid

    logger.debug(f"Beginning to create evaluations for validator {validator_hotkey}.")
    for agent_row in agents:
        version_id = agent_row[0]
        
        # Check if evaluation already exists
        logger.debug(f"Checking database for existing evaluation for version {version_id} and validator {validator_hotkey}.")
        existing_evaluation = await conn.fetchrow(
            "SELECT evaluation_id FROM evaluations "
            "WHERE version_id = $1 AND validator_hotkey = $2",
            version_id, validator_hotkey
        )
        logger.debug(f"Completed check for existing evaluation for version {version_id} and validator {validator_hotkey}.")
        
        if existing_evaluation:
            logger.debug(f"Evaluation already exists for version {version_id} and validator {validator_hotkey}. Skipping creation of evaluation.")
            continue
        logger.debug(f"No existing evaluation found for version {version_id} and validator {validator_hotkey}. Creating new evaluation.")
        
        # Create new evaluation
        evaluation_id = str(uuid.uuid4())
        logger.debug(f"Created UUID for new evaluation: {evaluation_id}. Inserting new evaluation into database.")
        
        await conn.execute("""
            INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, status, created_at)
            VALUES ($1, $2, $3, $4, $5)
        """, evaluation_id, version_id, validator_hotkey, 'waiting', datetime.now(timezone.utc))
        
        logger.debug(f"Successfully inserted new evaluation {evaluation_id} into database.")
        
        evaluations_created += 1
    
    logger.debug(f"Completed creation of evaluations for validator {validator_hotkey}. Created {evaluations_created} evaluations.")
    
    return evaluations_created

@db_operation
async def start_evaluation(conn: asyncpg.Connection, evaluation_id: str) -> Evaluation:
    logger.debug(f"Attempting to start evaluation {evaluation_id}.")
    result = await conn.fetchrow(
        "UPDATE evaluations SET status = 'running', started_at = NOW() WHERE evaluation_id = $1 RETURNING *",
        evaluation_id
    )

    if not result:
        logger.warning(f"Attempted to start evaluation {evaluation_id} but it was not found.")
        raise Exception(f"No evaluation with id {evaluation_id}")
    
    logger.debug(f"Successfully started evaluation {evaluation_id}.")

    return Evaluation(**dict(result))

@db_operation
async def finish_evaluation(conn: asyncpg.Connection, evaluation_id: str, errored: bool) -> Evaluation:
    logger.debug(f"Attempting to finish evaluation {evaluation_id}.")
    result = await conn.execute(
        "UPDATE evaluations SET status = $1, finished_at = NOW() WHERE evaluation_id = $2 RETURNING *",
        'completed' if not errored else 'error',
        evaluation_id
    )

    if not result:
        logger.warning(f"Attempted to finish evaluation {evaluation_id} but it was not found.")
        raise Exception(f"No evaluation with id {evaluation_id}")
    
    logger.debug(f"Successfully finished evaluation {evaluation_id}.")

    return Evaluation(**dict(result))

@db_operation
async def get_evaluation_by_evaluation_id(conn: asyncpg.Connection, evaluation_id: str) -> Evaluation:
    logger.debug(f"Attempting to get evaluation {evaluation_id} from the database.")
    result = await conn.fetchrow(
        "SELECT evaluation_id, version_id, validator_hotkey, status, terminated_reason, created_at, started_at, finished_at, score  "
        "FROM evaluations WHERE evaluation_id = $1",
        evaluation_id
    )

    if not result:
        logger.warning(f"Attempted to get evaluation {evaluation_id} from the database but it was not found.")
        raise Exception(f"No evaluation with id {evaluation_id}")
    
    logger.debug(f"Successfully retrieved evaluation {evaluation_id} from the database.")

    return Evaluation(**dict(result))
    
@db_operation
async def get_evaluations_by_version_id(conn: asyncpg.Connection, version_id: str) -> List[Evaluation]:
    result = await conn.fetch(
        "SELECT evaluation_id, version_id, validator_hotkey, status, terminated_reason, created_at, started_at, finished_at, score "
        "FROM evaluations WHERE version_id = $1 ORDER BY created_at DESC",
        version_id
    )

    return [Evaluation(**dict(row)) for row in result]

@db_operation
async def get_evaluations_for_agent_version(conn: asyncpg.Connection, version_id: str) -> list[EvaluationsWithHydratedRuns]:
    evaluation_rows = await conn.fetch("""
        SELECT 
            e.evaluation_id,
            e.version_id,
            e.validator_hotkey,
            e.status,
            e.terminated_reason,
            e.created_at,
            e.started_at,
            e.finished_at,
            e.score,
            COALESCE(
                array_agg(
                    json_build_object(
                        'run_id', er.run_id::text,
                        'evaluation_id', er.evaluation_id::text,
                        'swebench_instance_id', er.swebench_instance_id,
                        'status', er.status,
                        'response', er.response,
                        'error', er.error,
                        'pass_to_fail_success', er.pass_to_fail_success,
                        'fail_to_pass_success', er.fail_to_pass_success,
                        'pass_to_pass_success', er.pass_to_pass_success,
                        'fail_to_fail_success', er.fail_to_fail_success,
                        'solved', er.solved,
                        'started_at', er.started_at,
                        'sandbox_created_at', er.sandbox_created_at,
                        'patch_generated_at', er.patch_generated_at,
                        'eval_started_at', er.eval_started_at,
                        'result_scored_at', er.result_scored_at,
                        'cancelled_at', er.cancelled_at
                    ) ORDER BY er.started_at
                ) FILTER (WHERE er.run_id IS NOT NULL),
                '{}'::json[]
            ) as evaluation_runs
        FROM evaluations e
        LEFT JOIN evaluation_runs er ON e.evaluation_id = er.evaluation_id 
            AND er.status != 'cancelled'
        WHERE e.version_id = $1
        GROUP BY e.evaluation_id, e.version_id, e.validator_hotkey, e.status, e.terminated_reason, e.created_at, e.started_at, e.finished_at, e.score
        ORDER BY e.created_at DESC
    """, version_id)
    
    evaluations = []
    for row in evaluation_rows:
        # Convert JSON objects to EvaluationRun objects
        evaluation_runs = []
        for run_data in row[9]:  # evaluation_runs is at index 9
            evaluation_run = EvaluationRun(
                run_id=run_data['run_id'],
                evaluation_id=run_data['evaluation_id'],
                swebench_instance_id=run_data['swebench_instance_id'],
                status=run_data['status'],
                response=run_data['response'],
                error=run_data['error'],
                pass_to_fail_success=run_data['pass_to_fail_success'],
                fail_to_pass_success=run_data['fail_to_pass_success'],
                pass_to_pass_success=run_data['pass_to_pass_success'],
                fail_to_fail_success=run_data['fail_to_fail_success'],
                solved=run_data['solved'],
                started_at=run_data['started_at'],
                sandbox_created_at=run_data['sandbox_created_at'],
                patch_generated_at=run_data['patch_generated_at'],
                eval_started_at=run_data['eval_started_at'],
                result_scored_at=run_data['result_scored_at'],
                cancelled_at=run_data['cancelled_at']
            )
            evaluation_runs.append(evaluation_run)
        
        hydrated_evaluation = EvaluationsWithHydratedRuns(
            evaluation_id=row[0],
            version_id=row[1],
            validator_hotkey=row[2],
            status=row[3],
            terminated_reason=row[4],
            created_at=row[5],
            started_at=row[6],
            finished_at=row[7],
            score=row[8],
            evaluation_runs=evaluation_runs
        )
        evaluations.append(hydrated_evaluation)
    
    return evaluations

@db_operation
async def get_next_evaluation_for_validator(conn: asyncpg.Connection, validator_hotkey: str) -> Optional[Evaluation]:
    logger.debug(f"Fetching next evaluation from database for validator {validator_hotkey}.")

    result = await conn.fetchrow(
        """
            SELECT e.evaluation_id, e.version_id, e.validator_hotkey, e.status, e.terminated_reason, e.created_at, e.started_at, e.finished_at, e.score
            FROM evaluations e
            JOIN miner_agents ma ON e.version_id = ma.version_id
            WHERE e.validator_hotkey = $1
            AND e.status = 'waiting' 
            -- AND ma.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_miners)
            ORDER BY e.created_at ASC 
            LIMIT 1;
        """,
        validator_hotkey
    )

    if not result:
        logger.debug(f"No next evaluation found for validator {validator_hotkey}.")
        return None

    logger.debug(f"Found next evaluation for validator {validator_hotkey}: {dict(result)['evaluation_id']}.")

    return Evaluation(**dict(result)) 

@db_operation
async def get_running_evaluations(conn: asyncpg.Connection) -> List[Evaluation]:
    result = await conn.fetch(
        "SELECT evaluation_id, version_id, validator_hotkey, status, terminated_reason, created_at, started_at, finished_at, score "
        "FROM evaluations WHERE status = 'running'",
    )

    return [Evaluation(**dict(row)) for row in result]

@db_operation
async def get_running_evaluation_by_validator_hotkey(conn: asyncpg.Connection, validator_hotkey: str) -> Optional[Evaluation]:
    result = await conn.fetchrow(
        """
            SELECT evaluation_id, version_id, validator_hotkey, status, terminated_reason, created_at, started_at, finished_at, score
            FROM evaluations
            WHERE validator_hotkey = $1 
            AND status = 'running' 
            ORDER BY created_at ASC 
            LIMIT 1;
        """,
        validator_hotkey
    )

    if not result:
        return None

    return Evaluation(**dict(result)) 

@db_operation
async def get_queue_info(conn: asyncpg.Connection, validator_hotkey: str, length: int = 10) -> List[Evaluation]:
    """Get a list of the queued evaluations for a given validator"""
    result = await conn.fetch(
        "SELECT evaluation_id, version_id, validator_hotkey, status, terminated_reason, created_at, started_at, finished_at, score "
        "FROM evaluations WHERE status = 'waiting' AND validator_hotkey = $1 "
        "ORDER BY created_at DESC "
        "LIMIT $2",
        validator_hotkey,
        length
    )

    return [Evaluation(**dict(row)) for row in result]

'''
Evaluation Creation/Upserts
'''
@db_operation
async def reset_evaluation(conn: asyncpg.Connection, evaluation_id: str) -> None:
    """
    Reset an evaluation to waiting, and cancel any evaluation runs.
    """
    await conn.execute("""
        WITH cancelled_runs AS (
            UPDATE evaluation_runs SET status = 'cancelled', cancelled_at = NOW() WHERE evaluation_id = $1
        )
        UPDATE evaluations SET status = 'waiting', started_at = NULL, finished_at = NULL, score = NULL, terminated_reason = NULL WHERE evaluation_id = $1
    """, evaluation_id)
    
    logger.debug(f"Successfully reset evaluation {evaluation_id}.")

