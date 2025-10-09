import uuid
from datetime import datetime
from typing import List, Tuple
from uuid import UUID

import asyncpg

from api.queries.evaluation_run import create_evaluation_run
from api.queries.evaluation_set import get_latest_set_id, get_all_problem_names_of_group_in_set
from api.src.backend.db_manager import db_operation
from api.src.backend.db_manager import db_transaction
from models.evaluation import Evaluation, EvaluationStatus
from models.evaluation_run import EvaluationRun, EvaluationRunStatus
from models.evaluation_set import EvaluationSetGroup


@db_operation
async def create_evaluation(conn: asyncpg.connection.Connection, evaluation: Evaluation) -> None:
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
        evaluation.evaluation_id,
        evaluation.agent_id,
        evaluation.validator_hotkey,
        evaluation.set_id,
        evaluation.created_at,
    )


@db_transaction
async def create_new_evaluation_and_evaluation_runs(
    conn: asyncpg.Connection,
    agent_id: UUID,
    validator_hotkey: str,
    set_id: int = None
) -> Tuple[Evaluation, List[EvaluationRun]]:
    # If no set_id is provided, use the latest set_id
    if set_id is None:
        set_id = await get_latest_set_id()

    set_group = EvaluationSetGroup.from_validator_hotkey(validator_hotkey)
    problem_names = await get_all_problem_names_of_group_in_set(set_id, set_group)

    evaluation = Evaluation(
        evaluation_id=uuid.uuid4(),
        agent_id=agent_id,
        validator_hotkey=validator_hotkey,
        set_id=set_id,
        created_at=datetime.now(),
    )
    print(f"CREATING EVALUATION WITH ID: {evaluation.evaluation_id}")
    await create_evaluation(conn, evaluation)
    print(f"FINISHED CREATING EVALUATION WITH ID: {evaluation.evaluation_id}")


    evaluation_runs = [
        EvaluationRun(
            evaluation_run_id=uuid.uuid4(),
            evaluation_id=evaluation.evaluation_id,
            problem_name=problem_name,
            status=EvaluationRunStatus.pending,
            created_at=datetime.now(),
        )
        for problem_name in problem_names
    ]

    for evaluation_run in evaluation_runs:
        await create_evaluation_run(conn, evaluation_run)

    return evaluation, evaluation_runs


@db_transaction
async def get_evaluation_runs_for_evaluation(conn: asyncpg.connection.Connection, evaluation_id: int) -> List[EvaluationRun]:
    """Get all evaluation runs for a given evaluation run_id."""
    response = await conn.fetch(
        """
        SELECT *
        FROM evaluation_runs
        WHERE evaluation_id = $1
        """,
        evaluation_id
    )
    evaluation_runs = [
        EvaluationRun(**row) for row in response
    ]
    return evaluation_runs


@db_operation
async def get_evaluations_by_status(conn: asyncpg.Connection, status: EvaluationStatus) -> List[Evaluation]:
    results = await conn.fetch(
        """
        SELECT
            evaluation_id,
            agent_id,
            validator_hotkey,
            set_id,
            created_at,
            finished_at
        FROM 
            evaluations_hydrated 
        WHERE
            status = $1
        """,
        status.value
    )

    return [Evaluation(**result) for result in results]