import uuid
from datetime import datetime
from typing import List, Tuple
from uuid import UUID

import asyncpg
from api.queries.evaluation_run import create_evaluation_run
from api.queries.evaluation_set import get_latest_set_id, get_all_problems_of_group_in_set
from api.src.backend.db_manager import db_transaction
from models.evaluation import Evaluation
from models.evaluation_run import EvaluationRun, EvaluationRunStatus
from models.evaluation_set import EvaluationSetGroup


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
    problem_names = await get_all_problems_of_group_in_set(set_id, set_group)

    evaluation = Evaluation(
        evaluation_id=uuid.uuid4(),
        agent_id=agent_id,
        validator_hotkey=validator_hotkey,
        set_id=set_id,
        created_at=datetime.now(),
    )

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
        await create_evaluation_run(evaluation_run)

    return evaluation, evaluation_runs