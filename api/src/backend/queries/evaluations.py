from typing import Optional, List
import logging
import json

import asyncpg

from api.src.backend.db_manager import db_operation, db_transaction
from api.src.backend.entities import Evaluation, EvaluationRun, EvaluationsWithHydratedRuns, EvaluationsWithHydratedUsageRuns, EvaluationRunWithUsageDetails, AgentStatus
from api.src.backend.queries.evaluation_runs import get_runs_with_usage_for_evaluation
from api.src.backend.entities import EvaluationStatus

logger = logging.getLogger(__name__)


@db_operation
async def get_evaluation_by_evaluation_id(conn: asyncpg.Connection, evaluation_id: str) -> Evaluation:
    logger.debug(f"Attempting to get evaluation {evaluation_id} from the database.")
    result = await conn.fetchrow(
        "SELECT * FROM evaluations WHERE evaluation_id = $1",
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
async def get_evaluations_for_agent_version(conn: asyncpg.Connection, version_id: str, set_id: Optional[int] = None) -> list[EvaluationsWithHydratedRuns]:
    if set_id is None:
        set_id = await conn.fetchval("SELECT MAX(set_id) FROM evaluation_sets")

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
            e.screener_score,
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
        WHERE e.version_id = $1
        AND e.set_id = $2
        -- AND (
        --     (e.validator_hotkey NOT LIKE 'screener-%' AND e.validator_hotkey NOT LIKE 'i-0%')  -- Non-screener evaluations
        --     OR (
        --         (e.validator_hotkey LIKE 'screener-%' OR e.validator_hotkey LIKE 'i-0%')  -- Screener evaluations
        --         AND e.status IN ('completed', 'pruned', 'running')  -- Only successful/running screener evaluations
        --     )
        -- )
        GROUP BY e.evaluation_id, e.version_id, e.validator_hotkey, e.set_id, e.status, e.terminated_reason, e.created_at, e.started_at, e.finished_at, e.score
        ORDER BY e.created_at DESC
    """, version_id, set_id)
    
    evaluations = []
    for row in evaluation_rows:
        # Convert JSON objects to EvaluationRun objects
        evaluation_runs = []
        for run_data in row[11]:  # evaluation_runs is at index 11
            run_data = json.loads(run_data)
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
            screener_score=row[10],
            evaluation_runs=evaluation_runs
        )
        evaluations.append(hydrated_evaluation)
    
    return evaluations

@db_operation
async def get_evaluations_with_usage_for_agent_version(conn: asyncpg.Connection, version_id: str, set_id: Optional[int] = None, fast: bool = False) -> list[EvaluationsWithHydratedUsageRuns]:
    if set_id is None:
        set_id = await conn.fetchval("SELECT MAX(set_id) FROM evaluation_sets")

    if fast:
        # Fast path: single query with JSON aggregations
        evaluation_rows = await conn.fetch("""
            WITH inf AS (
                SELECT
                    run_id,
                    SUM(cost)          AS cost,
                    SUM(total_tokens)  AS total_tokens,
                    COUNT(*)           AS num_inference_calls,
                    MAX(model)         AS model
                FROM inferences
                GROUP BY run_id
            )
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
                e.screener_score,
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
                            'cancelled_at', er.cancelled_at,
                            'cost', i.cost,
                            'total_tokens', i.total_tokens,
                            'model', i.model,
                            'num_inference_calls', i.num_inference_calls
                        ) ORDER BY er.started_at
                    ) FILTER (WHERE er.run_id IS NOT NULL),
                    '{}'::json[]
                ) as evaluation_runs
            FROM evaluations e
            LEFT JOIN evaluation_runs er ON e.evaluation_id = er.evaluation_id 
            LEFT JOIN inf i ON er.run_id = i.run_id
            WHERE e.version_id = $1
            AND e.set_id = $2
            -- AND (
            --     (e.validator_hotkey NOT LIKE 'screener-%' AND e.validator_hotkey NOT LIKE 'i-0%')  -- Non-screener evaluations
            --     OR (
            --         (e.validator_hotkey LIKE 'screener-%' OR e.validator_hotkey LIKE 'i-0%')  -- Screener evaluations
            --         AND e.status IN ('completed', 'pruned', 'running')  -- Only successful/running screener evaluations
            --     )
            -- )
            GROUP BY e.evaluation_id, e.version_id, e.validator_hotkey, e.set_id, e.status, e.terminated_reason, e.created_at, e.started_at, e.finished_at, e.score
            ORDER BY e.created_at DESC
        """, version_id, set_id)
        
        evaluations = []
        for row in evaluation_rows:
            # Convert JSON objects to EvaluationRunWithUsageDetails objects
            evaluation_runs = []
            for run_data in row[11]:  # evaluation_runs is at index 11
                run_data = json.loads(run_data)
                evaluation_run = EvaluationRunWithUsageDetails(
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
                    cancelled_at=run_data['cancelled_at'],
                    cost=run_data['cost'],
                    total_tokens=run_data['total_tokens'],
                    model=run_data['model'],
                    num_inference_calls=run_data['num_inference_calls']
                )
                evaluation_runs.append(evaluation_run)
            
            hydrated_evaluation = EvaluationsWithHydratedUsageRuns(
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
                screener_score=row[10],
                evaluation_runs=evaluation_runs
            )
            evaluations.append(hydrated_evaluation)
        
        return evaluations
    
    # Original slower path for backward compatibility
    evaluations: list[EvaluationsWithHydratedUsageRuns] = []

    evaluation_rows = await conn.fetch("""
        (
            -- Get all non-screener evaluations
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
                score,
                screener_score
            FROM evaluations 
            WHERE version_id = $1
            AND set_id = $2
            -- AND (
            --     (validator_hotkey NOT LIKE 'screener-%' AND validator_hotkey NOT LIKE 'i-0%')  -- Non-screener evaluations
            --     OR (
            --         (validator_hotkey LIKE 'screener-%' OR validator_hotkey LIKE 'i-0%')  -- Screener evaluations
            --         AND status IN ('completed', 'pruned', 'running')  -- Only successful/running screener evaluations
            --     )
            -- )
        )
        
        -- UNION ALL
        -- 
        -- (
        --     -- Get only the latest screener evaluation
        --     SELECT 
        --         evaluation_id,
        --         version_id,
        --         validator_hotkey,
        --         set_id,
        --         status,
        --         terminated_reason,
        --         created_at,
        --         started_at,
        --         finished_at,
        --         score,
        --         screener_score
        --     FROM evaluations 
        --     WHERE version_id = $1
        --     AND set_id = $2
        --     AND (validator_hotkey LIKE 'screener-%' OR validator_hotkey LIKE 'i-0%')
        --     AND status NOT IN ('completed', 'running')  -- Only errored screener evaluations
        --     ORDER BY created_at DESC
        --     LIMIT 1
        -- )
        ORDER BY created_at DESC
        """,
        version_id, set_id
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
            screener_score=evaluation_row[10],
            evaluation_runs=evaluation_runs
        )

        evaluations.append(hydrated_evaluation)
    
    return evaluations

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
async def does_validator_have_running_evaluation(
    conn: asyncpg.Connection, 
    validator_hotkey: str
) -> bool:
    return await conn.fetchval(
        """
        SELECT EXISTS(
            SELECT 1
            FROM evaluations
            WHERE (status = 'running' AND validator_hotkey = $1)
        );
        """,
        validator_hotkey
    )

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
        "ORDER BY screener_score DESC NULLS LAST, created_at ASC "
        "LIMIT $2",
        validator_hotkey,
        length
    )

    return [Evaluation(**dict(row)) for row in result]

@db_operation
async def get_agent_name_from_version_id(conn: asyncpg.Connection, version_id: str) -> Optional[str]:
    """Get agent name for a given version_id"""
    return await conn.fetchval("""
        SELECT agent_name 
        FROM miner_agents 
        WHERE version_id = $1
    """, version_id)

@db_operation
async def get_miner_hotkey_from_version_id(conn: asyncpg.Connection, version_id: str) -> Optional[str]:
    """Get miner hotkey for a given version_id"""
    return await conn.fetchval("""
        SELECT miner_hotkey 
        FROM miner_agents 
        WHERE version_id = $1
    """, version_id)
