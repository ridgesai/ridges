from typing import List
from datetime import datetime
from utils.database import db_operation, DatabaseConnection
from models.evaluation_set import EvaluationSetGroup, EvaluationSetProblem



@db_operation
async def get_latest_set_id(conn: DatabaseConnection) -> int:
    return await conn.fetchval("SELECT MAX(set_id) FROM evaluation_sets")

@db_operation
async def get_latest_set_created_at(conn: DatabaseConnection) -> datetime:
    return await conn.fetchval("SELECT MIN(created_at) FROM evaluation_sets WHERE set_id = (SELECT MAX(set_id) FROM evaluation_sets)")



@db_operation
async def get_all_problem_names_in_set_group_in_set_id(conn: DatabaseConnection, set_id: int, set_group: EvaluationSetGroup) -> List[str]:
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
async def get_all_evaluation_set_problems_in_latest_set_id(conn: DatabaseConnection) -> List[EvaluationSetProblem]:
    results = await conn.fetch(
        """
        SELECT set_id, set_group, problem_name, created_at
        FROM evaluation_sets
        WHERE set_id = (SELECT MAX(set_id) FROM evaluation_sets)
        """
    )

    return [EvaluationSetProblem(**result) for result in results]