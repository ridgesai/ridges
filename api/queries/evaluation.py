import uuid
import asyncpg

from uuid import UUID
from datetime import datetime
from typing import List, Tuple
from models.evaluation_set import EvaluationSetGroup
from utils.database import db_operation, db_transaction
from models.evaluation import Evaluation, EvaluationStatus
from api.queries.evaluation_run import create_evaluation_run
from models.evaluation_run import EvaluationRun, EvaluationRunStatus
from api.queries.evaluation_set import get_latest_set_id, get_all_problem_names_of_group_in_set



@db_operation
async def create_evaluation(conn: asyncpg.Connection, agent_id: UUID, validator_hotkey: str, set_id: int) -> UUID:
    evaluation_id = uuid.uuid4()

    await conn.execute(
        """
        INSERT INTO evaluations (
            evaluation_id,
            agent_id,
            validator_hotkey,
            set_id,
            created_at
        ) VALUES ($1, $2, $3, $4, $5)
        """,
        evaluation_id,
        agent_id,
        validator_hotkey,
        set_id,
        datetime.now()
    )

    return evaluation_id



@db_transaction
async def create_new_evaluation_and_evaluation_runs(conn: asyncpg.Connection, agent_id: UUID, validator_hotkey: str, set_id: int = None) -> Tuple[Evaluation, List[EvaluationRun]]:
    if set_id is None:
        set_id = await get_latest_set_id()

    set_group = EvaluationSetGroup.from_validator_hotkey(validator_hotkey)
    problem_names = await get_all_problem_names_of_group_in_set(set_id, set_group)

    evaluation_id = await create_evaluation(
        agent_id,
        validator_hotkey,
        set_id
    )

    for problem_name in problem_names:
        await create_evaluation_run(
            evaluation_id,
            problem_name
        )

    return await get_evaluation_by_id(evaluation_id), await get_all_evaluation_runs_for_evaluation_id(evaluation_id)



@db_operation
async def get_all_evaluation_runs_for_evaluation_id(conn: asyncpg.Connection, evaluation_id: int) -> List[EvaluationRun]:
    rows = await conn.fetch(
        """
        SELECT *
        FROM evaluation_runs
        WHERE evaluation_id = $1
        """,
        evaluation_id
    )

    return [EvaluationRun(**row) for row in rows]



@db_operation
async def get_evaluations_by_status(conn: asyncpg.Connection, status: EvaluationStatus) -> List[Evaluation]:
    results = await conn.fetch(
        """
        SELECT
            evaluation_id,
            agent_id,
            validator_hotkey,
            set_id
        FROM evaluations_hydrated 
        WHERE status = $1
        """,
        status.value
    )

    return [Evaluation(**result) for result in results]



@db_operation
async def get_evaluation_by_id(conn: asyncpg.Connection, evaluation_id: int) -> Evaluation:
    response = await conn.fetchrow(
        """
        SELECT *
        FROM evaluations
        WHERE evaluation_id = $1
        """,
        evaluation_id,
    )

    return Evaluation(**response)