from typing import Optional

import asyncpg
from api.src.backend.entities import EvaluationRun, EvaluationRunWithUsageDetails
from api.src.backend.db_manager import db_operation


@db_operation
async def store_evaluation_run(conn: asyncpg.Connection, evaluation_run: EvaluationRun) -> EvaluationRun:
    """
    Store or update an evaluation run. The evaluation score is automatically updated by a database trigger.
    """
    await conn.execute(
        """
        INSERT INTO evaluation_runs (
            run_id, evaluation_id, swebench_instance_id, status, response, error,
            pass_to_fail_success, fail_to_pass_success, pass_to_pass_success, 
            fail_to_fail_success, solved, started_at, sandbox_created_at, 
            patch_generated_at, eval_started_at, result_scored_at, cancelled_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
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
            result_scored_at = EXCLUDED.result_scored_at,
            cancelled_at = EXCLUDED.cancelled_at
    """,
        evaluation_run.run_id,
        evaluation_run.evaluation_id,
        evaluation_run.swebench_instance_id,
        evaluation_run.status.value,
        evaluation_run.response,
        evaluation_run.error,
        evaluation_run.pass_to_fail_success,
        evaluation_run.fail_to_pass_success,
        evaluation_run.pass_to_pass_success,
        evaluation_run.fail_to_fail_success,
        evaluation_run.solved,
        evaluation_run.started_at,
        evaluation_run.sandbox_created_at,
        evaluation_run.patch_generated_at,
        evaluation_run.eval_started_at,
        evaluation_run.result_scored_at,
        evaluation_run.cancelled_at,
    )

    return evaluation_run

@db_operation
async def update_evaluation_run(conn: asyncpg.Connection, evaluation_run: EvaluationRun) -> EvaluationRun:
    """
    Update an evaluation run. The evaluation score is automatically updated by a database trigger.
    """
    await conn.execute(
        """
        UPDATE evaluation_runs SET 
            response = $1,
            error = $2,
            pass_to_fail_success = $3,
            fail_to_pass_success = $4,
            pass_to_pass_success = $5,
            fail_to_fail_success = $6,
            solved = $7,
            status = $8,
            started_at = $9,
            sandbox_created_at = $10,
            patch_generated_at = $11,
            eval_started_at = $12,
            result_scored_at = $13,
            cancelled_at = $14
        WHERE run_id = $1
        """,
        evaluation_run.response,
        evaluation_run.error,
        evaluation_run.pass_to_fail_success,
        evaluation_run.fail_to_pass_success,
        evaluation_run.pass_to_pass_success,
        evaluation_run.fail_to_fail_success,
        evaluation_run.solved,
        evaluation_run.status.value,
        evaluation_run.sandbox_created_at,
        evaluation_run.patch_generated_at,
        evaluation_run.eval_started_at,
        evaluation_run.result_scored_at,
        evaluation_run.cancelled_at,
        evaluation_run.run_id,
    )

@db_operation
async def get_runs_for_evaluation(
    conn: asyncpg.Connection, evaluation_id: str, include_cancelled: bool = False
) -> list[EvaluationRun]:
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
                result_scored_at,
                cancelled_at
            FROM evaluation_runs 
            WHERE evaluation_id = $1
            AND (status != 'cancelled' OR ($2 AND status = 'cancelled'))
            ORDER BY started_at
        """,
        evaluation_id,
        include_cancelled,
    )

    evaluation_runs = [EvaluationRun(**dict(run_row)) for run_row in run_rows]

    return evaluation_runs

@db_operation
async def all_runs_finished(conn: asyncpg.Connection, evaluation_id: str) -> bool:
    return await conn.fetchval("SELECT COUNT(*) FROM evaluation_runs WHERE result_scored_at IS NOT NULL AND evaluation_id = $1", evaluation_id) == 0

@db_operation
async def get_runs_with_usage_for_evaluation(conn: asyncpg.Connection, evaluation_id: str) -> list[EvaluationRunWithUsageDetails]:
    run_rows = await conn.fetch(
        """
            WITH inf AS (
                SELECT
                    run_id,
                    SUM(cost)          AS cost,
                    SUM(total_tokens)  AS total_tokens,
                    COUNT(*)           AS num_inference_calls,
                    MAX(model)         AS model        -- assumes a run uses one model
                FROM inferences
                GROUP BY run_id
            )
            SELECT
                e.run_id,
                e.evaluation_id,
                e.swebench_instance_id,
                e.status,
                e.response,
                e.error,
                e.pass_to_fail_success,
                e.fail_to_pass_success,
                e.pass_to_pass_success,
                e.fail_to_fail_success,
                e.solved,
                e.started_at,
                e.sandbox_created_at,
                e.patch_generated_at,
                e.eval_started_at,
                e.result_scored_at,
                e.cancelled_at,
                i.cost,
                i.total_tokens,
                i.model,
                i.num_inference_calls
            FROM evaluation_runs  e
            LEFT JOIN inf         i USING (run_id)        -- join on the PK only
            WHERE e.evaluation_id = $1
            AND e.status != 'cancelled'
            ORDER BY e.started_at;
        """,
        evaluation_id
    )

    evaluation_runs = [
        EvaluationRunWithUsageDetails(
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
            result_scored_at=run_row[15],
            cancelled_at=run_row[16],
            cost=run_row[17],
            total_tokens=run_row[18],
            model=run_row[19],
            num_inference_calls=run_row[20]
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
            result_scored_at,
            cancelled_at
        FROM evaluation_runs 
        WHERE run_id = $1
        """,
        run_id,
    )

    if not run_row:
        return None

    run = EvaluationRun(**dict(run_row))

    return run
