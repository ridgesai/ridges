import asyncpg

from uuid import UUID
from typing import Optional
from models.evaluation_runs import EvaluationRun
from api.src.backend.db_manager import db_operation



@db_operation
async def get_evaluation_run_by_id(conn: asyncpg.Connection, evaluation_run_id: UUID) -> Optional[EvaluationRun]:
    result = await conn.fetchrow("""
        SELECT *
        FROM evaluation_runs
        WHERE evaluation_run_id = $1
    """, evaluation_run_id)

    if not result:
        return None
    
    return EvaluationRun(**dict(result))