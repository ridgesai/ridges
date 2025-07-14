from typing import Optional

import asyncpg
from api.src.backend.entities import EvaluationRun
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
