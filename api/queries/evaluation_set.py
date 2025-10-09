import asyncpg

from typing import List
from utils.database import db_operation
from models.evaluation_set import EvaluationSetGroup, EvaluationSetProblem



@db_operation
async def get_latest_set_id(conn: asyncpg.Connection) -> int:
    result = await conn.fetchrow("SELECT MAX(set_id) as latest_set_id FROM evaluation_sets")
    return result["latest_set_id"]



@db_operation
async def get_all_problem_names_of_group_in_set(conn: asyncpg.Connection, set_id: int, set_group: EvaluationSetGroup) -> List[str]:
    results = await conn.fetch(
        """
        SELECT problem_name
        FROM evaluation_sets
        WHERE set_id = $1 AND set_group = $2
        ORDER BY problem_name
        """,
        set_id,
        set_group.value
    )
    return [row["problem_name"] for row in results]



@db_operation
async def get_all_problems_in_latest_set(conn: asyncpg.Connection) -> List[EvaluationSetProblem]:
    results = await conn.fetch(
        """
        SELECT set_id, set_group, problem_name
        FROM evaluation_sets
        WHERE set_id = (SELECT MAX(set_id) FROM evaluation_sets)
        """
    )
    return [EvaluationSetProblem(**result) for result in results]