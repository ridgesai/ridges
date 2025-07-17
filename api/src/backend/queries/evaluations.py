from typing import Optional, List
import logging
import uuid

import asyncpg
from datetime import datetime, timezone

from api.src.backend.db_manager import db_operation
from api.src.backend.entities import Evaluation, EvaluationRun, EvaluationsWithHydratedRuns, EvaluationsWithHydratedUsageRuns
from api.src.backend.queries.agents import get_agents_awaiting_screening
from api.src.backend.queries.evaluation_runs import get_runs_with_usage_for_evaluation

logger = logging.getLogger(__name__)

@db_operation
async def create_evaluation(conn: asyncpg.Connection, version_id: str, validator_hotkey: str) -> Optional[Evaluation]:
    logger.debug(f"Attempting to create evaluation for version {version_id} and validator {validator_hotkey}.")

    # Check if running evaluation exists for this validator
    running_evaluation = await get_running_evaluation_by_validator_hotkey(validator_hotkey)
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

    # Set waiting miner_agents for this miner_hotkey to replaced
    await conn.execute("""
        UPDATE miner_agents 
        SET status = 'replaced' 
        WHERE miner_hotkey = (
            SELECT miner_hotkey 
            FROM miner_agents 
            WHERE version_id = $1
        )
        AND status = 'waiting'
    """, version_id)
    
    # Get the latest set_id
    latest_set_id_result = await conn.fetchrow("SELECT MAX(set_id) as max_set_id FROM evaluation_sets")
    latest_set_id = latest_set_id_result['max_set_id'] if latest_set_id_result and latest_set_id_result['max_set_id'] is not None else 0
    
    evaluation = await conn.fetchrow("""
        INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, set_id, status, created_at)
        VALUES ($1, $2, $3, $4, $5, NOW())
        RETURNING *
    """, str(uuid.uuid4()), version_id, validator_hotkey, latest_set_id, 'waiting')

    logger.debug(f"Successfully created evaluation for version {version_id} and validator {validator_hotkey}.")

    return Evaluation(**dict(evaluation))

async def create_next_evaluation_for_screener(validator_hotkey: str) -> Optional[Evaluation]:
    agents_awaiting_screening = await get_agents_awaiting_screening()

    if not agents_awaiting_screening:
        return None
    
    evaluation = await create_evaluation(agents_awaiting_screening[0].version_id, validator_hotkey)
    if not evaluation:
        return None
    
    return evaluation

@db_operation
async def create_evaluations_for_validator(conn: asyncpg.Connection, validator_hotkey: str) -> int:
    logger.debug(f"Beginning to create evaluations for validator {validator_hotkey}.")

    logger.debug(f"Fetching agent versions from database for validator {validator_hotkey}.")
    agents = await conn.fetch(
        "SELECT ma.version_id "
        "FROM miner_agents ma "
        "WHERE ma.status = 'evaluating' "
        "ORDER BY ma.created_at DESC"
    )
    logger.debug(f"Fetched {len(agents)} agent versions from database for validator {validator_hotkey}.")

    if not agents:
        logger.debug(f"No agent versions found for validator {validator_hotkey}. No evaluations created.")
        return 0
    
    # Get the latest set_id
    latest_set_id_result = await conn.fetchrow("SELECT MAX(set_id) as max_set_id FROM evaluation_sets")
    latest_set_id = latest_set_id_result['max_set_id'] if latest_set_id_result and latest_set_id_result['max_set_id'] is not None else 0
    
    evaluations_created = 0 

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
            INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, set_id, status, created_at)
            VALUES ($1, $2, $3, $4, $5, $6)
        """, evaluation_id, version_id, validator_hotkey, latest_set_id, 'waiting', datetime.now(timezone.utc))
        
        logger.debug(f"Successfully inserted new evaluation {evaluation_id} into database.")
        
        evaluations_created += 1
    
    logger.debug(f"Completed creation of evaluations for validator {validator_hotkey}. Created {evaluations_created} evaluations.")
    
    return evaluations_created

@db_operation
async def start_evaluation(conn: asyncpg.Connection, evaluation_id: str, screener: bool) -> Evaluation:
    logger.debug(f"Attempting to start evaluation {evaluation_id}.")
    result = await conn.fetchrow(
        "UPDATE evaluations SET status = 'running', started_at = NOW() WHERE evaluation_id = $1 RETURNING *",
        evaluation_id
    )

    await conn.execute("""
        UPDATE miner_agents 
        SET status = $1
        WHERE version_id = (SELECT version_id FROM evaluations WHERE evaluation_id = $2)
    """, 'screening' if screener else 'evaluating', evaluation_id)

    if not result:
        logger.warning(f"Attempted to start evaluation {evaluation_id} but it was not found.")
        raise Exception(f"No evaluation with id {evaluation_id}")
    
    logger.debug(f"Successfully started evaluation {evaluation_id}.")

    return Evaluation(**dict(result))

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

    # If there are no more running evaluations for the version id, set the miner agent to waiting
    await conn.execute("""
        UPDATE miner_agents 
        SET status = 'waiting' 
        WHERE version_id = (SELECT version_id FROM evaluations WHERE evaluation_id = $1)
        AND NOT EXISTS (
            SELECT 1 FROM evaluations 
            WHERE version_id = (SELECT version_id FROM evaluations WHERE evaluation_id = $1)
            AND status = 'running'
        )
    """, evaluation_id)
    
    logger.debug(f"Successfully reset evaluation {evaluation_id}.")

@db_operation
async def cancel_screening_evaluation(conn: asyncpg.Connection, evaluation_id: str) -> None:
    """
    Cancel a screening evaluation. This is so if a screener never reconnects, a new 
    screening evaluation can be created for another screener instance.
    This is the only way a miner agent can be awaiting screening—a screener disconnects.
    """
    await conn.execute("""
        WITH cancelled_runs AS (
            UPDATE evaluation_runs 
            SET status = 'cancelled', cancelled_at = NOW() 
            WHERE evaluation_id = $1
        ),
        updated_evaluation AS (
            UPDATE evaluations 
            SET status = 'error', 
                started_at = NULL, 
                finished_at = NOW(),
                score = NULL, 
                terminated_reason = 'screener-disconnected'
            WHERE evaluation_id = $1 AND validator_hotkey LIKE 'i-0%'
            RETURNING version_id
        )
        UPDATE miner_agents 
        SET status = 'awaiting_screening' 
        WHERE version_id = (SELECT version_id FROM updated_evaluation)
    """, evaluation_id)
    
    logger.debug(f"Successfully cancelled screening evaluation {evaluation_id}.")


@db_operation
async def finish_evaluation(conn: asyncpg.Connection, evaluation_id: str, errored: bool) -> Evaluation:
    logger.debug(f"Attempting to finish evaluation {evaluation_id}.")
    result = await conn.fetchrow(
        "UPDATE evaluations SET status = $1, finished_at = NOW() WHERE evaluation_id = $2 RETURNING *",
        'completed' if not errored else 'error',
        evaluation_id
    )

    await conn.execute("""
        UPDATE miner_agents 
        SET status = 'scored' 
        WHERE version_id = (SELECT version_id FROM evaluations WHERE evaluation_id = $1)
        AND NOT EXISTS (
            SELECT 1 FROM evaluations 
            WHERE version_id = (SELECT version_id FROM evaluations WHERE evaluation_id = $1)
            AND status NOT IN ('completed', 'replaced', 'timedout', 'error')
        )
    """, evaluation_id)

    if not result:
        logger.warning(f"Attempted to finish evaluation {evaluation_id} but it was not found.")
        raise Exception(f"No evaluation with id {evaluation_id}")
    
    logger.debug(f"Successfully finished evaluation {evaluation_id}.")

    return Evaluation(**dict(result))

@db_operation
async def get_evaluation_by_evaluation_id(conn: asyncpg.Connection, evaluation_id: str) -> Evaluation:
    logger.debug(f"Attempting to get evaluation {evaluation_id} from the database.")
    result = await conn.fetchrow(
        "SELECT evaluation_id, version_id, validator_hotkey, set_id, status, terminated_reason, created_at, started_at, finished_at, score  "
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
        "SELECT * "
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
            e.set_id,
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
        GROUP BY e.evaluation_id, e.version_id, e.validator_hotkey, e.set_id, e.status, e.terminated_reason, e.created_at, e.started_at, e.finished_at, e.score
        ORDER BY e.created_at DESC
    """, version_id)
    
    evaluations = []
    for row in evaluation_rows:
        # Convert JSON objects to EvaluationRun objects
        evaluation_runs = []
        for run_data in row[10]:  # evaluation_runs is at index 10
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
            set_id=row[3],
            status=row[4],
            terminated_reason=row[5],
            created_at=row[6],
            started_at=row[7],
            finished_at=row[8],
            score=row[9],
            evaluation_runs=evaluation_runs
        )
        evaluations.append(hydrated_evaluation)
    
    return evaluations

@db_operation
async def get_evaluations_with_usage_for_agent_version(conn: asyncpg.Connection, version_id: str) -> list[EvaluationsWithHydratedUsageRuns]:
    evaluations: list[EvaluationsWithHydratedUsageRuns] = []

    evaluation_rows = await conn.fetch("""
            SELECT 
                evaluation_id,
                version_id,
                validator_hotkey,
                set_id,
                status,
                terminated_reason,
                created_at,
                started_at,
                finished_at,
                score
            FROM evaluations 
            WHERE version_id = $1
            ORDER BY created_at DESC
        """,
        version_id
    )
    
    for evaluation_row in evaluation_rows:
        evaluation_id = evaluation_row[0]

        evaluation_runs = await get_runs_with_usage_for_evaluation(evaluation_id=evaluation_id)

        hydrated_evaluation = EvaluationsWithHydratedUsageRuns(
            evaluation_id=evaluation_id,
            version_id=evaluation_row[1],
            validator_hotkey=evaluation_row[2],
            set_id=evaluation_row[3],
            status=evaluation_row[4],
            terminated_reason=evaluation_row[5],
            created_at=evaluation_row[6],
            started_at=evaluation_row[7],
            finished_at=evaluation_row[8],
            score=evaluation_row[9],
            evaluation_runs=evaluation_runs
        )

        evaluations.append(hydrated_evaluation)
    
    return evaluations

@db_operation
async def get_next_evaluation_for_validator(conn: asyncpg.Connection, validator_hotkey: str) -> Optional[Evaluation]:
    logger.debug(f"Fetching next evaluation from database for validator {validator_hotkey}.")

    result = await conn.fetchrow(
        """
            SELECT e.*
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
async def get_next_evaluation_for_screener(conn: asyncpg.Connection) -> Optional[Evaluation]:
    result = await conn.fetchrow(
        "SELECT * "
        "FROM evaluations WHERE status = 'waiting' AND validator_hotkey LIKE 'i-0%' "
        "ORDER BY created_at ASC LIMIT 1"
    )

    if not result:
        return None

    return Evaluation(**dict(result)) 

@db_operation
async def get_running_evaluations(conn: asyncpg.Connection) -> List[Evaluation]:
    result = await conn.fetch("SELECT * FROM evaluations WHERE status = 'running'")

    return [Evaluation(**dict(row)) for row in result]

@db_operation
async def get_running_evaluation_by_validator_hotkey(conn: asyncpg.Connection, validator_hotkey: str) -> Optional[Evaluation]:
    result = await conn.fetchrow(
        """
            SELECT *
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
async def get_running_evaluation_by_miner_hotkey(conn: asyncpg.Connection, miner_hotkey: str) -> Optional[Evaluation]:
    result = await conn.fetchrow(
        """
        SELECT e.*
        FROM evaluations e
        JOIN miner_agents ma ON e.version_id = ma.version_id
        WHERE ma.miner_hotkey = $1
        AND e.status = 'running'
        ORDER BY e.created_at ASC
        """,
        miner_hotkey
    )
    if not result:
        return None
    if len(result) > 1:
        validators = ", ".join([row[2] for row in result])
        logger.warning(f"Multiple running evaluations found for miner {miner_hotkey} on validators {validators}")
        return None
    
    return Evaluation(**dict(result[0]))

@db_operation
async def get_queue_info(conn: asyncpg.Connection, validator_hotkey: str, length: int = 10) -> List[Evaluation]:
    """Get a list of the queued evaluations for a given validator"""
    result = await conn.fetch(
        "SELECT * "
        "FROM evaluations WHERE status = 'waiting' AND validator_hotkey = $1 "
        "ORDER BY created_at DESC "
        "LIMIT $2",
        validator_hotkey,
        length
    )

    return [Evaluation(**dict(row)) for row in result]
