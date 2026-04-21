import json
from datetime import datetime
from typing import List

import asyncpg

from models.evaluation_set import EvaluationSetGroup, EvaluationSetProblem, NewEvaluationSetProblem
from queries._row_parsing import parse_jsonb_fields
from utils.database import DatabaseConnection, db_operation


def _parse_evaluation_set_problem_from_row(row: asyncpg.Record) -> EvaluationSetProblem:
    return EvaluationSetProblem(**parse_jsonb_fields(row, "execution_spec"))


@db_operation
async def get_latest_set_id(conn: DatabaseConnection) -> int | None:
    return await conn.fetchval("SELECT MAX(set_id) FROM evaluation_sets")


@db_operation
async def get_set_created_at(conn: DatabaseConnection, set_id: int) -> datetime:
    return await conn.fetchval("SELECT MIN(created_at) FROM evaluation_sets WHERE set_id = $1", set_id)


@db_operation
async def get_all_problem_names_in_set_group_in_set_id(
    conn: DatabaseConnection, set_id: int, set_group: EvaluationSetGroup
) -> list[tuple[str, str | None]]:
    results = await conn.fetch(
        """
        SELECT problem_name, problem_suite_name
        FROM evaluation_sets
        WHERE set_id = $1 AND set_group = $2
        ORDER BY problem_name
        """,
        set_id,
        set_group.value,
    )

    return [(row["problem_name"], row["problem_suite_name"]) for row in results]


@db_operation
async def get_all_evaluation_set_problems_in_set_group_in_set_id(
    conn: DatabaseConnection, set_id: int, set_group: EvaluationSetGroup
) -> List[EvaluationSetProblem]:
    results = await conn.fetch(
        """
        SELECT *
        FROM evaluation_sets
        WHERE set_id = $1 AND set_group = $2
        ORDER BY problem_name
        """,
        set_id,
        set_group.value,
    )

    return [_parse_evaluation_set_problem_from_row(result) for result in results]


@db_operation
async def get_all_evaluation_set_problems_for_set_id(
    conn: DatabaseConnection, set_id: int
) -> list[EvaluationSetProblem]:
    results = await conn.fetch(
        """
        SELECT *
        FROM evaluation_sets
        WHERE set_id = $1
        ORDER BY set_group, problem_name
        """,
        set_id,
    )

    return [_parse_evaluation_set_problem_from_row(result) for result in results]


@db_operation
async def create_evaluation_set_problems(
    conn: DatabaseConnection, set_id: int, problems: List[NewEvaluationSetProblem]
) -> None:
    await conn.executemany(
        """
        INSERT INTO evaluation_sets (
            set_id,
            set_group,
            problem_name,
            problem_suite_name,
            benchmark_family,
            execution_spec,
            created_at
        ) VALUES ($1, $2, $3, $4, $5, $6::jsonb, NOW())
        """,
        [
            (
                set_id,
                problem.set_group.value,
                problem.problem_name,
                problem.problem_suite_name or problem.benchmark_family,
                problem.benchmark_family,
                json.dumps(problem.execution_spec),
            )
            for problem in problems
        ],
    )
