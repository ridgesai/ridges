import asyncpg

from typing import List
from api.src.backend.db_manager import db_operation



@db_operation
async def get_latest_set_id(conn: asyncpg.Connection) -> int:
    result = await conn.fetchrow("SELECT MAX(set_id) as latest_set_id FROM evaluation_sets")
    return result["latest_set_id"] or 0

@db_operation
async def get_all_problems_of_type_in_set(conn: asyncpg.Connection, set_id: int, type: str) -> List[str]:
    results = await conn.fetch("""
        SELECT swebench_instance_id
        FROM evaluation_sets
        WHERE set_id = $1 AND type = $2
        ORDER BY swebench_instance_id
    """, set_id, type)
    return [row["swebench_instance_id"] for row in results]