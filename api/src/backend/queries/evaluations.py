from typing import Optional, List
import logging
from uuid import UUID

import asyncpg
from datetime import datetime, timezone

from api.src.backend.db_manager import db_operation
from api.src.backend.entities import Evaluation, EvaluationRun, EvaluationsWithHydratedRuns

logger = logging.getLogger(__name__)

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

@db_operation
async def get_current_evaluations(conn: asyncpg.Connection) -> List[Evaluation]:
    result = await conn.fetch(
        "SELECT evaluation_id, version_id, validator_hotkey, status, terminated_reason, created_at, started_at, finished_at, score "
        "FROM evaluations WHERE status = 'running'",
    )

    return [Evaluation(**dict(row)) for row in result]


'''
Evaluation Creation/Upserts
'''
@db_operation
async def store_evaluation(conn: asyncpg.Connection, evaluation: Evaluation):
    """
    Stores or updates new evaluation. 
    If score is None, the existing score is preserved (allowing triggers to calculate it).
    """
    logger.debug(f"Attempting to store evaluation {evaluation.evaluation_id} in the database.")

    await conn.execute("""
        INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, status, created_at, started_at, finished_at, terminated_reason, score)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ON CONFLICT (evaluation_id) DO UPDATE 
        SET status = EXCLUDED.status,
            started_at = EXCLUDED.started_at,
            finished_at = EXCLUDED.finished_at,
            terminated_reason = EXCLUDED.terminated_reason,
            score = CASE WHEN EXCLUDED.score IS NOT NULL THEN EXCLUDED.score ELSE evaluations.score END
    """, evaluation.evaluation_id, evaluation.version_id, evaluation.validator_hotkey, evaluation.status.value, 
        evaluation.created_at, evaluation.started_at, evaluation.finished_at, evaluation.terminated_reason, evaluation.score) 
    
    logger.debug(f"Successfully stored evaluation {evaluation.evaluation_id} in the database.")

@db_operation
async def store_evaluation_run(conn: asyncpg.Connection, evaluation_run: EvaluationRun):
    """
    Store or update an evaluation run. The evaluation score is automatically updated by a database trigger.
    """
    await conn.execute("""
        INSERT INTO evaluation_runs (
            run_id, evaluation_id, swebench_instance_id, status, response, error,
            pass_to_fail_success, fail_to_pass_success, pass_to_pass_success, 
            fail_to_fail_success, solved, started_at, sandbox_created_at, 
            patch_generated_at, eval_started_at, result_scored_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
        ON CONFLICT (run_id) DO UPDATE 
        SET status = EXCLUDED.status,
            response = EXCLUDED.response,
            error = EXCLUDED.error,
            pass_to_fail_success = EXCLUDED.pass_to_fail_success,
            fail_to_pass_success = EXCLUDED.fail_to_pass_success,
            pass_to_pass_success = EXCLUDED.pass_to_pass_success,
            fail_to_fail_success = EXCLUDED.fail_to_fail_success,
            solved = EXCLUDED.solved,
            sandbox_created_at = EXCLUDED.sandbox_created_at,
            patch_generated_at = EXCLUDED.patch_generated_at,
            eval_started_at = EXCLUDED.eval_started_at,
            result_scored_at = EXCLUDED.result_scored_at
    """, evaluation_run.run_id, evaluation_run.evaluation_id, evaluation_run.swebench_instance_id,
        evaluation_run.status.value, evaluation_run.response, evaluation_run.error,
        evaluation_run.pass_to_fail_success, evaluation_run.fail_to_pass_success,
        evaluation_run.pass_to_pass_success, evaluation_run.fail_to_fail_success,
        evaluation_run.solved, evaluation_run.started_at, evaluation_run.sandbox_created_at,
        evaluation_run.patch_generated_at, evaluation_run.eval_started_at, evaluation_run.result_scored_at)
    
    # Score is now automatically updated by database trigger when evaluation_runs.solved is updated

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

# TODO: this kind of does the work of updating stuff manually, we should decide if we want db ops to be general (like store_agent, which handles updates by adding a conflict check) or at this level
@db_operation
async def start_evaluation(conn: asyncpg.Connection, evaluation_id: str) -> Evaluation:
    result = await conn.fetchrow("""
        UPDATE evaluations 
        SET status = 'running', started_at = NOW()
        WHERE evaluation_id = $1
        RETURNING *;
    """, evaluation_id)
    
    if not result:
        raise Exception(f"No evaluation found with id {evaluation_id}")
    
    return Evaluation(**dict(result))

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
async def delete_evaluation_runs(conn: asyncpg.Connection, evaluation_id: str) -> int:
    result = await conn.execute("""
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
    return result.split()[-1] if result else 0

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
async def get_runs_for_evaluation(conn: asyncpg.Connection, evaluation_id: str) -> list[EvaluationRun]:
    run_rows = await conn.fetch(
        """
            SELECT 
                run_id,
                evaluation_id,
                swebench_instance_id,
                status,
                response,
                error,
                pass_to_fail_success,
                fail_to_pass_success,
                pass_to_pass_success,
                fail_to_fail_success,
                solved,
                started_at,
                sandbox_created_at,
                patch_generated_at,
                eval_started_at,
                result_scored_at
            FROM evaluation_runs 
            WHERE evaluation_id = $1
            ORDER BY started_at
        """,
        evaluation_id
    )

    evaluation_runs = [
        EvaluationRun(
            run_id=str(run_row[0]),
            evaluation_id=str(run_row[1]),
            swebench_instance_id=run_row[2],
            status=run_row[3],
            response=run_row[4],
            error=run_row[5],
            pass_to_fail_success=run_row[6],
            fail_to_pass_success=run_row[7],
            pass_to_pass_success=run_row[8],
            fail_to_fail_success=run_row[9],
            solved=run_row[10],
            started_at=run_row[11],
            sandbox_created_at=run_row[12],
            patch_generated_at=run_row[13],
            eval_started_at=run_row[14],
            result_scored_at=run_row[15]
        ) for run_row in run_rows
    ]

    return evaluation_runs

@db_operation
async def get_run_by_id(conn: asyncpg.Connection, run_id: str) -> Optional[EvaluationRun]:
    run_row = await conn.fetchrow(
        """
        SELECT 
            run_id,
            evaluation_id,
            swebench_instance_id,
            status,
            response,
            error,
            pass_to_fail_success,
            fail_to_pass_success,
            pass_to_pass_success,
            fail_to_fail_success,
            solved,
            started_at,
            sandbox_created_at,
            patch_generated_at,
            eval_started_at,
            result_scored_at
        FROM evaluation_runs 
        WHERE run_id = $1
        """,
        run_id
    )

    if not run_row:
        return None
    
    run = EvaluationRun(
        run_id=str(run_row[0]),
        evaluation_id=str(run_row[1]),
        swebench_instance_id=run_row[2],
        status=run_row[3],
        response=run_row[4],
        error=run_row[5],
        pass_to_fail_success=run_row[6],
        fail_to_pass_success=run_row[7],
        pass_to_pass_success=run_row[8],
        fail_to_fail_success=run_row[9],
        solved=run_row[10],
        started_at=run_row[11],
        sandbox_created_at=run_row[12],
        patch_generated_at=run_row[13],
        eval_started_at=run_row[14],
        result_scored_at=run_row[15]
    )

    return run

@db_operation
async def get_evaluations_for_agent_version(conn: asyncpg.Connection, version_id: str) -> list[EvaluationsWithHydratedRuns]:
    evaluations: list[EvaluationsWithHydratedRuns] = []

    evaluation_rows = await conn.fetch("""
            SELECT 
                evaluation_id,
                version_id,
                validator_hotkey,
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

        evaluation_runs = await get_runs_for_evaluation(evaluation_id=evaluation_id)

        hydrated_evaluation = EvaluationsWithHydratedRuns(
            evaluation_id=evaluation_id,
            version_id=evaluation_row[1],
            validator_hotkey=evaluation_row[2],
            status=evaluation_row[3],
            terminated_reason=evaluation_row[4],
            created_at=evaluation_row[5],
            started_at=evaluation_row[6],
            finished_at=evaluation_row[7],
            score=evaluation_row[8],
            evaluation_runs=evaluation_runs
        )

        evaluations.append(hydrated_evaluation)
    
    return evaluations

@db_operation
async def check_for_new_high_score(conn: asyncpg.Connection, version_id: UUID) -> dict:
    """
    Check if version_id scored higher than all approved agents.
    Uses LEFT JOIN to compare against approved_version_ids scores.
    
    Returns dict with:
    - high_score_detected: bool
    - agent details if high score detected
    - reason if no high score detected
    """
    logger.debug(f"Attempting to get the current agent's details and score from miner_agents for version {version_id}.")
    # Get the current agent's details and score from miner_agents
    agent_result = await conn.fetchrow("""
        SELECT agent_name, miner_hotkey, version_num, score
        FROM miner_agents 
        WHERE version_id = $1 AND score IS NOT NULL
    """, version_id)
    logger.debug(f"Successfully retrieved the current agent's details and score from miner_agents for version {version_id}.")
    
    if not agent_result:
        logger.debug(f"No agent found or no score available for version {version_id}.")
        return {
            "high_score_detected": False, 
            "reason": "Agent not found or no score available"
        }
    
    current_score = agent_result['score']
    logger.debug(f"Current agent's score for version {version_id} is {current_score}.")
    
    # Get the highest score among ALL approved agents using LEFT JOIN
    logger.debug(f"Attempting to get the highest score among ALL approved agents using LEFT JOIN.")
    max_approved_result = await conn.fetchrow("""
        SELECT MAX(e.score) as max_approved_score
        FROM approved_version_ids avi
        LEFT JOIN evaluations e ON avi.version_id = e.version_id  
        WHERE e.status = 'completed' AND e.score IS NOT NULL
    """)
    
    max_approved_score = max_approved_result['max_approved_score'] if max_approved_result else None
    logger.debug(f"The highest score among ALL approved agents is {max_approved_score}.")
    
    # Check if this beats all approved agents (ANY improvement triggers notification)
    if max_approved_score is None or current_score > max_approved_score:
        logger.info(f"🎯 HIGH SCORE DETECTED: {agent_result['agent_name']} scored {current_score:.4f} vs previous max {max_approved_score or 0.0:.4f}")
        return {
            "high_score_detected": True,
            "agent_name": agent_result['agent_name'],
            "miner_hotkey": agent_result['miner_hotkey'], 
            "version_id": str(version_id),
            "version_num": agent_result['version_num'],
            "new_score": current_score,
            "previous_max_score": max_approved_score or 0.0
        }

    return {
        "high_score_detected": False,
        "reason": f"Score {current_score:.4f} does not beat max approved score {max_approved_score:.4f}"
    }
