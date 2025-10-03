import uuid
import asyncpg

from api.src.backend.db_manager import db_transaction
from api.src.backend.new_queries.evaluation_runs import create_evaluation_run_for_evaluation_id
from api.src.backend.new_queries.evaluation_sets import get_all_problems_of_type_in_set, get_latest_set_id



@db_transaction
async def create_screener_evaluation_and_runs_for_agent(conn: asyncpg.Connection, version_id: str, screener_hotkey: str) -> uuid.UUID:
    # Figure out the screener number
    # TODO: Assumes screener_hotkey is valid (i.e., "screener-1-X" or "screener-2-X"). Maybe sanity check this?
    screener_class = int(screener_hotkey.split("-")[1])

    # First, get the latest set ID
    latest_set_id = await get_latest_set_id()

    # Second, create a new evaluation
    evaluation_id = str(uuid.uuid4())
    await conn.execute("""
        INSERT INTO evaluations (
            evaluation_id,
            version_id,
            validator_hotkey
            status,
            created_at,
            started_at,
            set_id,
            screener_score
        )
        VALUES (
            $1, -- evaluation_id
            $2, -- version_id
            $3, -- validator_hotkey
            "running", -- status
            NOW(), -- created_at
            NOW(), -- started_at
            $4, -- set_id
            $5 -- screener_score
        )
    """,
        evaluation_id,
        version_id,
        screener_hotkey,
        latest_set_id,
        None
    )

    # Third, get all the problems for the latest set ID
    problems = await get_all_problems_of_type_in_set(latest_set_id, f"screener-{screener_class}")

    # Fourth, create all the appropriate runs
    for problem in problems:
        await create_evaluation_run_for_evaluation_id(evaluation_id, problem)