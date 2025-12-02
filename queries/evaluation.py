import uuid
from typing import List, Tuple, Optional
from uuid import UUID

import utils.logger as logger
from queries.evaluation_run import create_evaluation_runs, get_all_evaluation_runs_in_evaluation_id
from queries.evaluation_set import get_latest_set_id, get_all_problem_names_in_set_group_in_set_id
from models.evaluation import Evaluation, EvaluationStatus, HydratedEvaluation
from models.evaluation_run import EvaluationRun, EvaluationRunStatus, EvaluationRunErrorCode
from models.evaluation_set import EvaluationSetGroup
from utils.database import db_operation, DatabaseConnection



@db_operation
async def create_evaluation(conn: DatabaseConnection, agent_id: UUID, validator_hotkey: str, set_id: int) -> UUID:
    evaluation_id = uuid.uuid4()

    await conn.execute(
        """
        INSERT INTO evaluations (
            evaluation_id,
            agent_id,
            validator_hotkey,
            set_id,
            created_at
        ) VALUES ($1, $2, $3, $4, NOW())
        """,
        evaluation_id,
        agent_id,
        validator_hotkey,
        set_id
    )

    logger.debug(f"Created evaluation {evaluation_id} for agent {agent_id} with validator hotkey {validator_hotkey} and set ID {set_id}")

    return evaluation_id



@db_operation
async def create_new_evaluation_and_evaluation_runs(conn: DatabaseConnection, agent_id: UUID, validator_hotkey: str, set_id: int = None) -> Tuple[Evaluation, List[EvaluationRun]]:
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

    await create_evaluation_runs(evaluation_id, problem_names)

    return await get_evaluation_by_id(evaluation_id), await get_all_evaluation_runs_in_evaluation_id(evaluation_id)



@db_operation
async def get_evaluation_by_id(conn: DatabaseConnection, evaluation_id: UUID) -> Evaluation:
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
async def get_hydrated_evaluation_by_id(conn: DatabaseConnection, evaluation_id: UUID) -> Optional[HydratedEvaluation]:
    result = await conn.fetchrow(
        """
        SELECT *
        FROM evaluations_hydrated
        WHERE evaluation_id = $1
        """,
        evaluation_id,
    )

    if result is None:
        return None

    return HydratedEvaluation(**result)

@db_operation
async def get_hydrated_evaluation_by_evaluation_run_id(conn: DatabaseConnection, evaluation_run_id: UUID) -> Optional[HydratedEvaluation]:
    result = await conn.fetchrow(
        """
        SELECT *
        FROM evaluations_hydrated
        WHERE evaluation_id = (SELECT evaluation_id FROM evaluation_runs WHERE evaluation_run_id = $1 LIMIT 1)
        """,
        evaluation_run_id,
    )

    if result is None:
        return None
    
    return HydratedEvaluation(**result)



@db_operation
async def get_evaluations_for_agent_id(conn: DatabaseConnection, agent_id: UUID) -> list[Evaluation]:
    results = await conn.fetch(
        """
        SELECT * FROM evaluations
        WHERE agent_id = $1
        """,
        agent_id
    )

    return [Evaluation(**evaluation) for evaluation in results]



@db_operation
async def update_unfinished_evaluation_runs_in_evaluation_id_to_errored(conn: DatabaseConnection, evaluation_id: UUID, error_message: str) -> None:
    await conn.execute(
        f"""
        UPDATE evaluation_runs
        SET
            status = '{EvaluationRunStatus.error.value}',
            error_code = CASE
                WHEN status = '{EvaluationRunStatus.pending.value}' THEN {EvaluationRunErrorCode.PLATFORM_RESTARTED_WHILE_PENDING.value}
                WHEN status = '{EvaluationRunStatus.initializing_agent.value}' THEN {EvaluationRunErrorCode.PLATFORM_RESTARTED_WHILE_INIT_AGENT.value}
                WHEN status = '{EvaluationRunStatus.running_agent.value}' THEN {EvaluationRunErrorCode.PLATFORM_RESTARTED_WHILE_INIT_AGENT.value}
                WHEN status = '{EvaluationRunStatus.initializing_eval.value}' THEN {EvaluationRunErrorCode.PLATFORM_RESTARTED_WHILE_INIT_EVAL.value}
                WHEN status = '{EvaluationRunStatus.running_eval.value}' THEN {EvaluationRunErrorCode.PLATFORM_RESTARTED_WHILE_RUNNING_EVAL.value}
            END,
            error_message = $2,
            finished_or_errored_at = NOW()
        WHERE evaluation_id = $1
        AND status NOT IN ('{EvaluationRunStatus.finished.value}', '{EvaluationRunStatus.error.value}')
        """,
        evaluation_id,
        error_message
    )

# Used to perform cleanup after a platform crash
@db_operation
async def set_all_unfinished_evaluation_runs_to_errored(conn: DatabaseConnection, error_message: str) -> None:
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
                ELSE {EvaluationRunErrorCode.VALIDATOR_INTERNAL_ERROR.value}
            END,
            error_message = $1,
            finished_or_errored_at = NOW()
        WHERE
            status NOT IN ('{EvaluationRunStatus.finished.value}', '{EvaluationRunStatus.error.value}')
        """,
        error_message
    )


@db_operation
async def update_evaluation_finished_at(conn: DatabaseConnection, evaluation_id: UUID) -> None:
    await conn.execute(
        """
        UPDATE evaluations
        SET finished_at = NOW()
        WHERE evaluation_id = $1
        """,
        evaluation_id
    )



@db_operation
async def get_num_successful_validator_evaluations_for_agent_id(conn: DatabaseConnection, agent_id: UUID) -> int:
    result = await conn.fetchrow(
        f"""
        SELECT COUNT(*) as num_successful_validator_evaluations
        FROM evaluations_hydrated
        WHERE 
            agent_id = $1
            AND status = '{EvaluationStatus.success.value}'
            AND evaluation_set_group = 'validator'::EvaluationSetGroup
        """,
        agent_id,
    )

    return result["num_successful_validator_evaluations"]