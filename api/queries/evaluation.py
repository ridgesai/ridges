import asyncpg

from uuid import UUID
from typing import List, Tuple 
from models.evaluation import Evaluation
from models.evaluation_run import EvaluationRun
from api.src.backend.db_manager import db_operation
from api.queries.evaluation_set import get_latest_set_id



@db_operation
async def create_new_evaluation_and_evaluation_runs(conn: asyncpg.Connection, agent_id: UUID, validator_hotkey: str, set_id: int = None) -> Tuple[Evaluation, List[EvaluationRun]]:
    # If no set_id is provided, use the latest set_id
    if set_id is None:
        set_id = await get_latest_set_id()

    # Detemine the set_group to use
    if validator_hotkey.startswith("screener-"):
        set_group = f"screener_{validator_hotkey.split("-")[1]}"
    else:
        set_group = "validator"

    # TODO: Alex

    # Probably want to make a create_new_evaluation_run_for_evaluation() function in evaluation_run.py too...

    pass