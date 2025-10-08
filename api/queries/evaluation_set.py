import asyncpg

from typing import List
from api.src.backend.db_manager import db_operation
from models.evaluation_set import EvaluationSetGroup


@db_operation
async def get_latest_set_id(conn: asyncpg.Connection) -> int:
    result = await conn.fetchrow("SELECT MAX(set_id) as latest_set_id FROM evaluation_sets")
    return result["latest_set_id"] or 0



@db_operation
async def get_all_problems_of_group_in_set(conn: asyncpg.Connection, set_id: int, set_group: EvaluationSetGroup) -> List[str]:
    results = await conn.fetch("""
        SELECT problem_name
        FROM evaluation_sets
        WHERE set_id = $1 AND set_group = $2
        ORDER BY problem_name
    """, set_id, set_group.value)
    return [row["problem_name"] for row in results]
