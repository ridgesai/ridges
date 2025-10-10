import uuid
from datetime import datetime
from typing import List, Tuple
from uuid import UUID

import asyncpg

import utils.logger as logger
from api.queries.evaluation_run import create_evaluation_run, get_all_evaluation_runs_in_evaluation_id
from api.queries.evaluation_set import get_latest_set_id, get_all_problem_names_in_set_group_in_set_id
from models.evaluation import Evaluation, EvaluationStatus, HydratedEvaluation
from models.evaluation_run import EvaluationRun, EvaluationRunStatus, EvaluationRunErrorCode
from models.evaluation_set import EvaluationSetGroup
from utils.database import db_operation, db_transaction


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

    logger.debug(f"Created evaluation {evaluation_id} for agent {agent_id} with validator hotkey {validator_hotkey} and set ID {set_id}")

    return evaluation_id



@db_transaction
async def create_new_evaluation_and_evaluation_runs(conn: asyncpg.Connection, agent_id: UUID, validator_hotkey: str, set_id: int = None) -> Tuple[Evaluation, List[EvaluationRun]]:
    if set_id is None:
        set_id = await get_latest_set_id()
    
    logger.debug(f"Creating new evaluation and evaluation runs for agent {agent_id} with validator hotkey {validator_hotkey} and set ID {set_id}")

    set_group = EvaluationSetGroup.from_validator_hotkey(validator_hotkey)
    problem_names = await get_all_problem_names_in_set_group_in_set_id(set_id, set_group)

    logger.debug(f"# of problems in set ID {set_id}, set group {set_group}: {len(problem_names)}")

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

    return await get_evaluation_by_id(evaluation_id), await get_all_evaluation_runs_in_evaluation_id(evaluation_id)



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



@db_operation
async def get_hydrated_evaluation_by_id(conn: asyncpg.Connection, evaluation_id: int) -> HydratedEvaluation:
    response = await conn.fetchrow(
        """
        SELECT *
        FROM evaluations_hydrated
        WHERE evaluation_id = $1
        """,
        evaluation_id,
    )

    return HydratedEvaluation(**response)


@db_operation
async def get_evaluations_for_agent_id(conn: asyncpg.Connection, agent_id: UUID) -> list[Evaluation]:
    results = await conn.fetch(
        """
        SELECT * FROM evaluations
        WHERE agent_id = $1
        """,
        agent_id
    )

    return [Evaluation(**evaluation) for evaluation in results]



@db_operation
async def mark_all_running_evaluation_runs_in_evaluation_id_as_errored(conn: asyncpg.Connection, evaluation_id: UUID, error_message: str) -> None:
    await conn.execute(
        f"""
        UPDATE evaluation_runs
        SET
            status = '{EvaluationRunStatus.error.value}',
            error_code = CASE
                WHEN status = '{EvaluationRunStatus.pending.value}' THEN {EvaluationRunErrorCode.VALIDATOR_FAILED_PENDING.value}
                WHEN status = '{EvaluationRunStatus.initializing_agent.value}' THEN {EvaluationRunErrorCode.VALIDATOR_FAILED_INIT_AGENT.value}
                WHEN status = '{EvaluationRunStatus.running_agent.value}' THEN {EvaluationRunErrorCode.VALIDATOR_FAILED_RUNNING_AGENT.value}
                WHEN status = '{EvaluationRunStatus.initializing_eval.value}' THEN {EvaluationRunErrorCode.VALIDATOR_FAILED_INIT_EVAL.value}
                WHEN status = '{EvaluationRunStatus.running_eval.value}' THEN {EvaluationRunErrorCode.VALIDATOR_FAILED_RUNNING_EVAL.value}
                ELSE {EvaluationRunErrorCode.VALIDATOR_UNKNOWN_PROBLEM.value}
            END,
            error_message = $2,
            finished_or_errored_at = $3
        WHERE evaluation_id = $1
        AND status NOT IN ('{EvaluationRunStatus.finished.value}', '{EvaluationRunStatus.error.value}')
        """,
        evaluation_id,
        error_message,
        datetime.now()
    )



@db_operation
async def record_evaluation_finished_at(conn: asyncpg.Connection, evaluation_id: int) -> None:
    await conn.execute(
        """
        UPDATE evaluations
        SET finished_at = NOW()
        WHERE evaluation_id = $1
        """,
        evaluation_id
    )

@db_operation
async def get_num_successful_validator_evaluations_for_agent_id(conn: asyncpg.Connection, agent_id: UUID) -> int:
    result = await conn.fetchrow(
        f"""
        SELECT COUNT(*) as num_successful_validator_evaluations
        FROM evaluations_hydrated
        WHERE 
            agent_id = $1
            AND status = '{EvaluationStatus.success.value}'
            AND validator_hotkey NOT LIKE 'screener-%'
        """,
        agent_id,
    )
    return result["num_successful_validator_evaluations"]
