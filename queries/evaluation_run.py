import asyncpg
import json
import utils.logger as logger

from uuid import UUID, uuid4
from typing import List, Optional
from utils.database import db_operation, DatabaseConnection
from models.evaluation_run import EvaluationRun, EvaluationRunStatus, EvaluationRunLogType



def _parse_evaluation_run_from_row(row: asyncpg.Record) -> EvaluationRun:
    # test_results is a jsonb column, as such, it is returned from asyncpg as a
    # string. We need to parse it for pydantic to be able to use it.
    row_dict = dict(row)
    row_dict["test_results"] = json.loads(row_dict["test_results"]) if row_dict["test_results"] else None
    return EvaluationRun(**row_dict)



@db_operation
async def get_evaluation_run_by_id(conn: DatabaseConnection, evaluation_run_id: UUID) -> Optional[EvaluationRun]:
    row = await conn.fetchrow(
        """
        SELECT *
        FROM evaluation_runs
        WHERE evaluation_run_id = $1
        """,
        evaluation_run_id
    )

    if not row:
        return None

    return _parse_evaluation_run_from_row(row)



@db_operation
async def get_evaluation_run_status_by_id(conn: DatabaseConnection, evaluation_run_id: UUID) -> Optional[EvaluationRunStatus]:
    status = await conn.fetchval(
        """
        SELECT status FROM evaluation_runs WHERE evaluation_run_id = $1
        """,
        evaluation_run_id)

    if status is None:
        return None

    return EvaluationRunStatus(status)



@db_operation
async def get_all_evaluation_runs_in_evaluation_id(conn: DatabaseConnection, evaluation_id: UUID) -> List[EvaluationRun]:
    rows = await conn.fetch(
        """
        SELECT *
        FROM evaluation_runs
        WHERE evaluation_id = $1
        """,
        evaluation_id
    )

    return [_parse_evaluation_run_from_row(row) for row in rows]



@db_operation
async def update_evaluation_run_by_id(conn: DatabaseConnection, evaluation_run: EvaluationRun) -> None:
    await conn.execute(
        """
        UPDATE evaluation_runs SET 
            status = $2,
            patch = $3,
            test_results = $4,
            error_code = $5,
            error_message = $6,
            started_initializing_agent_at = $7,
            started_running_agent_at = $8,
            started_initializing_eval_at = $9,
            started_running_eval_at = $10,
            finished_or_errored_at = $11
        WHERE evaluation_run_id = $1
        """,
        evaluation_run.evaluation_run_id,
        evaluation_run.status.value,
        evaluation_run.patch,
        json.dumps([test_result.model_dump() for test_result in evaluation_run.test_results]) if evaluation_run.test_results else None,
        evaluation_run.error_code,
        evaluation_run.error_message,
        evaluation_run.started_initializing_agent_at,
        evaluation_run.started_running_agent_at,
        evaluation_run.started_initializing_eval_at,
        evaluation_run.started_running_eval_at,
        evaluation_run.finished_or_errored_at
    )



@db_operation
async def create_evaluation_run(conn: DatabaseConnection, evaluation_id: UUID, problem_name: str) -> UUID:
    evaluation_run_id = uuid4()

    await conn.execute(
        """
        INSERT INTO evaluation_runs (
            evaluation_run_id,
            evaluation_id,
            problem_name,
            status,
            created_at
        ) VALUES ($1, $2, $3, $4, NOW())
        """,
        evaluation_run_id,
        evaluation_id,
        problem_name,
        EvaluationRunStatus.pending.value
    )

    logger.debug(f"Created evaluation run {evaluation_run_id} for evaluation {evaluation_id} with problem name {problem_name}")

    return evaluation_run_id



@db_operation
async def create_evaluation_runs(conn: DatabaseConnection, evaluation_id: UUID, problem_names: List[str]) -> None:
    await conn.executemany(
        """
        INSERT INTO evaluation_runs (
            evaluation_run_id,
            evaluation_id,
            problem_name,
            status,
            created_at
        ) VALUES ($1, $2, $3, $4, NOW())
        """,
        [(uuid4(), evaluation_id, problem_name, EvaluationRunStatus.pending.value) for problem_name in problem_names]
    )

    logger.debug(f"Created {len(problem_names)} evaluation runs for evaluation {evaluation_id}")



@db_operation
async def create_evaluation_run_log(conn: DatabaseConnection, evaluation_run_id: UUID, type: EvaluationRunLogType, logs: str) -> None:
    await conn.execute(
        """
        INSERT INTO evaluation_run_logs (
            evaluation_run_id,
            type,
            logs
        ) VALUES ($1, $2, $3)
        """,
        evaluation_run_id,
        type,
        logs.replace('\x00', '')
    )

    num_lines = len(logs.split('\n'))
    logger.debug(f"Created evaluation run log for evaluation run {evaluation_run_id} with type {type}, {num_lines} line(s), {len(logs)} character(s)")



@db_operation
async def check_if_evaluation_run_logs_exist(conn: DatabaseConnection, evaluation_run_id: UUID, type: EvaluationRunLogType) -> bool:
    return await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM evaluation_run_logs
            WHERE evaluation_run_id = $1 AND type = $2
        )
        """,
        evaluation_run_id,
        type.value
    )



@db_operation
async def get_evaluation_run_logs_by_id(conn: DatabaseConnection, evaluation_run_id: UUID, type: EvaluationRunLogType) -> Optional[str]:
    logs = await conn.fetchval(
        """
        SELECT logs FROM evaluation_run_logs
        WHERE type = $1
        and evaluation_run_id = $2
        """,
        type,
        evaluation_run_id
    )

    return logs