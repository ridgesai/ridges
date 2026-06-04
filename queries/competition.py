import asyncpg

from utils.database import DatabaseConnection, db_operation


@db_operation
async def get_competition_for_set(conn: DatabaseConnection, set_id: int) -> asyncpg.Record | None:
    """
    Retrieve competition details for a specific evaluation set."""
    return await conn.fetchrow(
        """
        SELECT name AS competition_name, start_date AS competition_start_date, end_date AS competition_end_date
        FROM competitions
        WHERE set_id = $1
        """,
        set_id,
    )
