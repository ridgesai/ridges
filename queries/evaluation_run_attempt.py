import logging
from typing import List
from uuid import UUID

import asyncpg

from models.evaluation_run import EvaluationRunAttempt
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
