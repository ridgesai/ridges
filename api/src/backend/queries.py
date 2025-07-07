from typing import Optional
import asyncpg
import logging
from functools import wraps
from api.src.backend.entities import Agent, AgentVersion, Evaluation, EvaluationRun

from datetime import datetime

logger = logging.getLogger(__name__)

# We need to import this from where it's defined globally
def db_operation(func):
    """Decorator to handle database operations with logging and transaction rollback"""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        # Import here to avoid circular imports
        from api.src.main import new_db

        async with new_db.acquire() as conn:
            async with conn.transaction():
                try:
                    return await func(conn, *args, **kwargs)
                except Exception as e:
                    logger.error(f"Database operation failed in {func.__name__}: {e}")
                    # Context manager will roll back transaction, reversing any failed commits
                    raise

    return wrapper

'''
Agent upload related functions
'''
@db_operation
async def create_agent(conn: asyncpg.Connection, agent: Agent):
    await conn.execute("""
        INSERT INTO agents (miner_hotkey, name, latest_version, created_at, last_updated)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (miner_hotkey) DO UPDATE 
        SET name = EXCLUDED.name,
            latest_version = EXCLUDED.latest_version,
            last_updated = EXCLUDED.last_updated
    """, agent.miner_hotkey, agent.name, agent.latest_version, agent.created_at, agent.last_updated)

@db_operation
async def store_agent_version(conn: asyncpg.Connection, agent_version: AgentVersion):
    await conn.execute("""
        INSERT INTO agent_versions (version_id, miner_hotkey, version_num, created_at, score)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (version_id) DO UPDATE 
        SET score = EXCLUDED.score
    """, agent_version.version_id, agent_version.miner_hotkey, agent_version.version_num, agent_version.created_at, agent_version.score)

# TODO: combine first query with second. this should be a batch call.
@db_operation
async def create_evaluations_for_validator(conn: asyncpg.Connection, validator_hotkey: str) -> int:
    agent_versions = await conn.fetch(
        "SELECT av.version_id "
        "FROM agent_versions av "
        "JOIN agents a ON av.miner_hotkey = a.miner_hotkey "
        "WHERE av.created_at >= NOW() - INTERVAL '24 hours' "
        "AND av.version_num = a.latest_version "
        "ORDER BY av.created_at DESC"
    )

    if not agent_versions:
        raise Exception("No recent agent versions found")
    
    evaluations_created = 0 
    import uuid

    for version_row in agent_versions:
        version_id = version_row[0]
        
        # Check if evaluation already exists
        existing_evaluation = await conn.fetchrow(
            "SELECT evaluation_id FROM evaluations "
            "WHERE version_id = $1 AND validator_hotkey = $2",
            version_id, validator_hotkey
        )
        
        if existing_evaluation:
            logger.debug(f"Evaluation already exists for version {version_id} and validator {validator_hotkey}")
            continue
        
        # Create new evaluation
        evaluation_id = str(uuid.uuid4())
        await conn.execute("""
            INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, status, created_at)
            VALUES ($1, $2, $3, $4, $5)
        """, evaluation_id, version_id, validator_hotkey, 'waiting', datetime.now())
        
        evaluations_created += 1
    
    return evaluations_created

@db_operation
async def get_agent_by_hotkey(conn: asyncpg.Connection, miner_hotkey: str) -> Optional[Agent]:
    """Get agent by hotkey. Auto-create evaluations for connected validators"""
    result = await conn.fetch(
        "SELECT miner_hotkey, name, latest_version, created_at, last_updated "
        "FROM agents WHERE miner_hotkey = $1",
        miner_hotkey
    )

    if not result:
        return None
    
    return Agent(**dict(result[0]))

'''
Websocket connection related endpoints
'''
# TODO: banning system
banned_hotkeys = ["5GWz1uK6jhmMbPK42dXvyepzq4gzorG1Km3NTMdyDGHaFDe9"]

@db_operation
async def get_next_evaluation(conn: asyncpg.Connection, validator_hotkey: str) -> Optional[Evaluation]:
    result = await conn.fetchrow(
        """
            SELECT e.evaluation_id, e.version_id, e.validator_hotkey, e.status, e.terminated_reason, e.created_at, e.started_at, e.finished_at, e.score
            FROM evaluations e
            JOIN agent_versions av ON e.version_id = av.version_id
            JOIN agents a ON av.agent_id = a.agent_id
            WHERE e.validator_hotkey = :validator_hotkey 
            AND e.status = 'waiting' 
            AND a.miner_hotkey != ALL(:banned_hotkeys)
            ORDER BY e.created_at ASC 
            LIMIT 1;
        """,
        validator_hotkey
    )

    if not result:
        return None

    return Evaluation(**dict(result)) 

@db_operation
async def get_agent_version(conn: asyncpg.Connection, version_id: str) -> AgentVersion:
    result = await conn.fetchrow(
        "SELECT version_id, miner_hotkey, version_num, created_at, score "
        "FROM agent_versions WHERE version_id = $1",
        version_id
    )

    if not result:
        raise Exception(f"No agent version with id {version_id}")
    
    return AgentVersion(**dict(result))

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
            SELECT *
            FROM evaluations
            WHERE validator_hotkey = :validator_hotkey 
            AND status = 'running' 
            ORDER BY e.created_at ASC 
            LIMIT 1;
        """,
        validator_hotkey
    )

    if not result:
        return None

    return Evaluation(**dict(result)) 

async def delete_evaluation_runs(conn: asyncpg.Connection, evaluation_id: str) -> int:
    await conn.execute(
        "DELETE FROM evaluation_runs WHERE evaluation_id = :evaluation_id ",
        evaluation_id
    ) 

'''
Validator data pulling
'''
@db_operation
async def get_evaluation(conn: asyncpg.Connection, evaluation_id: str) -> Evaluation:
    result = await conn.fetchrow(
        "SELECT evaluation_id, version_id, validator_hotkey, status, terminated_reason, created_at, started_at, finished_at, score  "
        "FROM evaluations WHERE evaluation_id = $1",
        evaluation_id
    )

    if not result:
        raise Exception(f"No evaluation with id {evaluation_id}")
    
    return Evaluation(**dict(result))

async def get_evaluations_by_version_id():
    pass 
    # - Get all evaluations for a version

async def get_queue_info():
    pass 
    # - Get queue position per validator

async def get_current_evaluations():
    pass 
    # - Get all running evaluations

async def get_latest_weights():
    pass 
    # - Get current network weights

'''
Evaluation Creation/Upserts
'''
@db_operation
async def store_evaluation(conn: asyncpg.Connection, evaluation: Evaluation):
    """
    Stores or updates new evaluation
    """
    await conn.execute("""
        INSERT INTO evaluations (evaluation_id, version_id, validator_hotkey, status, created_at, started_at, finished_at, terminated_reason, score)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ON CONFLICT (evaluation_id) DO UPDATE 
        SET status = EXCLUDED.status,
            started_at = EXCLUDED.started_at,
            finished_at = EXCLUDED.finished_at,
            terminated_reason = EXCLUDED.terminated_reason,
            score = EXCLUDED.score
    """, evaluation.evaluation_id, evaluation.version_id, evaluation.validator_hotkey, evaluation.status, 
        evaluation.created_at, evaluation.started_at, evaluation.finished_at, evaluation.terminated_reason, evaluation.score) 

@db_operation
async def store_evaluation_run(conn: asyncpg.Connection, evaluation_run: EvaluationRun):
    """
    Store or update an evaluation run and update the associated evaluation's score
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
        evaluation_run.status, evaluation_run.response, evaluation_run.error,
        evaluation_run.pass_to_fail_success, evaluation_run.fail_to_pass_success,
        evaluation_run.pass_to_pass_success, evaluation_run.fail_to_fail_success,
        evaluation_run.solved, evaluation_run.started_at, evaluation_run.sandbox_created_at,
        evaluation_run.patch_generated_at, evaluation_run.eval_started_at, evaluation_run.result_scored_at)
    
    # Update the score for the associated evaluation based on average of solved runs
    await conn.execute("""
        UPDATE evaluations 
        SET score = (
            SELECT AVG(CASE WHEN solved THEN 1.0 ELSE 0.0 END)
            FROM evaluation_runs 
            WHERE evaluation_id = $1
        )
        WHERE evaluation_id = $1
    """, evaluation_run.evaluation_id)

async def delete_evaluation_runs():
    pass 
    # - Clean up runs on disconnect


'''
Cleanup and maintainance
'''

async def clean_hanging_evaluations():
    pass 
    # - Reset stuck evaluations

async def clean_timed_out_evaluations():
    pass 
    # - Reset timed-out evaluations (>150min)

async def get_pool_status():
    pass 
    # - Monitor DB connection health
