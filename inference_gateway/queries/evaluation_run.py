import asyncpg

from uuid import UUID
from typing import Optional
from utils.database import db_operation
from models.evaluation_run import EvaluationRunStatus



@db_operation
async def get_evaluation_run_status_by_id(conn: asyncpg.Connection, evaluation_run_id: UUID) -> Optional[EvaluationRunStatus]:
    status = await conn.fetchval("""
        SELECT status FROM evaluation_runs WHERE evaluation_run_id = $1
    """, evaluation_run_id)

    if status is None:
        return None

    return EvaluationRunStatus(status)