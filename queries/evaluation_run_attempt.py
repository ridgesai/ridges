import logging
from typing import List
from uuid import UUID, uuid4

import asyncpg

from models.evaluation_run import EvaluationRunAttempt, EvaluationRunStatus
from utils.database import DatabaseConnection, db_operation

logger = logging.getLogger(__name__)


def _parse_attempt_from_row(row: asyncpg.Record) -> EvaluationRunAttempt:
    return EvaluationRunAttempt(**dict(row))


@db_operation
async def get_attempts_for_evaluation_run(
    conn: DatabaseConnection, evaluation_run_id: UUID
) -> List[EvaluationRunAttempt]:
    rows = await conn.fetch(
        """
        SELECT *
        FROM evaluation_run_attempts
        WHERE evaluation_run_id = $1
        ORDER BY attempt_number ASC
        """,
        evaluation_run_id,
    )

    return [_parse_attempt_from_row(row) for row in rows]


@db_operation
async def get_attempt_count_for_evaluation_run(conn: DatabaseConnection, evaluation_run_id: UUID) -> int:
    return await conn.fetchval(
        """
        SELECT COUNT(*)::int
        FROM evaluation_run_attempts
        WHERE evaluation_run_id = $1
        """,
        evaluation_run_id,
    )


@db_operation
async def create_next_attempt_and_reset_evaluation_run(
    conn: DatabaseConnection, evaluation_run_id: UUID
) -> EvaluationRunAttempt:
    """Insert attempt N+1 as pending and reset the evaluation_runs mirror, atomically."""

    async with conn.conn.transaction():
        row = await conn.fetchrow(
            """
            INSERT INTO evaluation_run_attempts (attempt_id, evaluation_run_id, attempt_number, status, created_at)
            SELECT $2, $1, COALESCE(MAX(attempt_number), 0) + 1, $3, NOW()
            FROM evaluation_run_attempts
            WHERE evaluation_run_id = $1
            RETURNING *
            """,
            evaluation_run_id,
            uuid4(),
            EvaluationRunStatus.pending.value,
        )
        await conn.execute(
            """
            UPDATE evaluation_runs SET
                status = $2,
                patch = NULL,
                test_results = NULL,
                verifier_reward = NULL,
                error_code = NULL,
                error_message = NULL,
                cost_usd = NULL,
                started_initializing_agent_at = NULL,
                started_running_agent_at = NULL,
                started_initializing_eval_at = NULL,
                started_running_eval_at = NULL,
                finished_or_errored_at = NULL
            WHERE evaluation_run_id = $1
            """,
            evaluation_run_id,
            EvaluationRunStatus.pending.value,
        )

    logger.info(f"Created attempt {row['attempt_number']} for evaluation run {evaluation_run_id} and reset mirror")

    return _parse_attempt_from_row(row)
