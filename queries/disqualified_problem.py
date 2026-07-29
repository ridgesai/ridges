from models.disqualified_problem import DisqualifiedProblem
from models.evaluation_set import EvaluationSetGroup
from utils.database import DatabaseConnection, db_operation


async def disqualify_problem(
    conn: DatabaseConnection,
    *,
    set_id: int,
    set_group: EvaluationSetGroup | str,
    problem_name: str,
    reason: str,
) -> DisqualifiedProblem:
    """Insert (or update the reason of) a disqualified problem. Runs in the caller's transaction."""
    group = set_group.value if isinstance(set_group, EvaluationSetGroup) else set_group
    row = await conn.fetchrow(
        """
        INSERT INTO disqualified_problems (set_id, set_group, problem_name, reason)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (set_id, set_group, problem_name) DO UPDATE SET reason = EXCLUDED.reason
        RETURNING *
        """,
        set_id,
        group,
        problem_name,
        reason,
    )
    return DisqualifiedProblem(**row)


@db_operation
async def get_disqualified_problem(
    conn: DatabaseConnection,
    set_id: int,
    set_group: EvaluationSetGroup | str,
    problem_name: str,
) -> DisqualifiedProblem | None:
    group = set_group.value if isinstance(set_group, EvaluationSetGroup) else set_group
    row = await conn.fetchrow(
        "SELECT * FROM disqualified_problems WHERE set_id = $1 AND set_group = $2 AND problem_name = $3",
        set_id,
        group,
        problem_name,
    )
    return DisqualifiedProblem(**row) if row is not None else None


@db_operation
async def problem_exists_in_set(
    conn: DatabaseConnection,
    set_id: int,
    set_group: EvaluationSetGroup | str,
    problem_name: str,
) -> bool:
    group = set_group.value if isinstance(set_group, EvaluationSetGroup) else set_group
    return await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM evaluation_sets
            WHERE set_id = $1 AND set_group = $2 AND problem_name = $3
        )
        """,
        set_id,
        group,
        problem_name,
    )
