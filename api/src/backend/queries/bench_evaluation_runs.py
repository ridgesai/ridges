import uuid
from datetime import datetime
from typing import Optional

import asyncpg
from api.src.backend.entities import EvaluationRun
from api.src.backend.db_manager import db_operation, db_transaction
from loggers.logging_utils import get_logger

logger = get_logger(__name__)


@db_operation
async def get_runs_for_benchmark_evaluation(
    conn: asyncpg.Connection, evaluation_id: str, include_cancelled: bool = False
) -> list[EvaluationRun]:
    """
    Get evaluation runs for a benchmark evaluation from the bench_evaluation_runs table.
    Same structure as regular evaluation_runs but for benchmark evaluations.
    """
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
            FROM bench_evaluation_runs 
            WHERE evaluation_id = $1
            AND (status != 'cancelled' OR ($2 AND status = 'cancelled'))
            ORDER BY started_at
        """,
        evaluation_id,
        include_cancelled,
    )

    evaluation_runs = [EvaluationRun(**dict(run_row)) for run_row in run_rows]

    return evaluation_runs
