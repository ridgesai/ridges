from typing import List
from datetime import datetime
from uuid import UUID
from utils.database import db_operation, DatabaseConnection
from models.evaluation_set import EvaluationSetGroup, EvaluationSetProblem, RawInfiniteSWEProblem



@db_operation
async def get_latest_set_id(conn: DatabaseConnection) -> int:
    return await conn.fetchval("SELECT MAX(set_id) FROM evaluation_sets")

@db_operation
async def get_set_created_at(conn: DatabaseConnection, set_id: int) -> datetime:
    return await conn.fetchval("SELECT MIN(created_at) FROM evaluation_sets WHERE set_id = $1", set_id)



@db_operation
async def get_all_problem_names_in_set_group_in_set_id(conn: DatabaseConnection, set_id: int, set_group: EvaluationSetGroup) -> list[tuple[str, str]]:
    results = await conn.fetch(
        """
        SELECT problem_name, problem_suite_name
        FROM evaluation_sets
        WHERE set_id = $1 AND set_group = $2
        ORDER BY problem_name
        """,
        set_id,
        set_group.value
    )
    
    return [(row["problem_name"], row["problem_suite_name"]) for row in results]



@db_operation
async def get_all_evaluation_set_problems_for_set_id(conn: DatabaseConnection, set_id: int) -> list[EvaluationSetProblem]:
    results = await conn.fetch(
        """
        SELECT *
        FROM evaluation_sets
        WHERE set_id = $1
        """,
        set_id
    )

    return [EvaluationSetProblem(**result) for result in results]

@db_operation
async def get_infinite_swe_problems(conn: DatabaseConnection, infinite_swe_problem_ids: list[UUID]) -> list[RawInfiniteSWEProblem]:
    results = await conn.fetch(
        """
        SELECT *
        FROM infinite_swe_problems
        WHERE id = ANY($1)
        """,
        infinite_swe_problem_ids
    )
    return [RawInfiniteSWEProblem(**result) for result in results]