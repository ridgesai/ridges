from typing import Optional, List
import logging

import asyncpg
from datetime import datetime

from api.src.backend.db_manager import db_operation
from api.src.backend.entities import Evaluation, EvaluationRun, EvaluationsWithHydratedRuns

logger = logging.getLogger(__name__)

@db_operation
async def get_evaluation_by_evaluation_id(conn: asyncpg.Connection, evaluation_id: str) -> Evaluation:
    result = await conn.fetchrow(
        "SELECT evaluation_id, version_id, validator_hotkey, status, terminated_reason, created_at, started_at, finished_at, score  "
        "FROM evaluations WHERE evaluation_id = $1",
        evaluation_id
    )

    if not result:
        raise Exception(f"No evaluation with id {evaluation_id}")
    
    return Evaluation(**dict(result))
    
@db_operation
async def get_evaluations_by_version_id(conn: asyncpg.Connection, version_id: str) -> List[Evaluation]:
    result = await conn.fetch(
        "SELECT evaluation_id, version_id, validator_hotkey, status, terminated_reason, created_at, started_at, finished_at, score "
        "FROM evaluations WHERE version_id = $1",
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

@db_operation
async def get_next_evaluation_for_validator(conn: asyncpg.Connection, validator_hotkey: str) -> Optional[Evaluation]:
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

@db_operation
async def delete_evaluation_runs(conn: asyncpg.Connection, evaluation_id: str) -> int:
    await conn.execute(
        "DELETE FROM evaluation_runs WHERE evaluation_id = :evaluation_id ",
        evaluation_id
    ) 

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
async def get_runs_for_evaluation(conn: asyncpg.Connection, evaluation_id: str) -> list[EvaluationRun]:
    run_rows = conn.fetch(
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

        evaluation_runs = get_runs_for_evaluation(evaluation_id=evaluation_id)

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
