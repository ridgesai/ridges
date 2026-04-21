import json
from typing import List, Optional
from uuid import UUID, uuid4

import asyncpg

import utils.logger as logger
from models.evaluation_run import EvaluationRun, EvaluationRunLogType, EvaluationRunStatus
from models.evaluation_set import EvaluationSetProblem
from queries._row_parsing import parse_jsonb_fields
from utils.database import DatabaseConnection, db_operation


def _parse_evaluation_run_from_row(row: asyncpg.Record) -> EvaluationRun:
    return EvaluationRun(**parse_jsonb_fields(row, "test_results", "execution_spec"))


@db_operation
async def get_evaluation_run_by_id(conn: DatabaseConnection, evaluation_run_id: UUID) -> Optional[EvaluationRun]:
    row = await conn.fetchrow(
        """
        SELECT *
        FROM evaluation_runs
        WHERE evaluation_run_id = $1
        """,
        evaluation_run_id,
    )

    if not row:
        return None

    return _parse_evaluation_run_from_row(row)


@db_operation
async def get_evaluation_run_status_by_id(
    conn: DatabaseConnection, evaluation_run_id: UUID
) -> Optional[EvaluationRunStatus]:
    status = await conn.fetchval(
        """
        SELECT status FROM evaluation_runs WHERE evaluation_run_id = $1
        """,
        evaluation_run_id,
    )

    if status is None:
        return None

    return EvaluationRunStatus(status)


@db_operation
async def get_all_evaluation_runs_in_evaluation_id(
    conn: DatabaseConnection, evaluation_id: UUID
) -> List[EvaluationRun]:
    rows = await conn.fetch(
        """
        SELECT *
        FROM evaluation_runs
        WHERE evaluation_id = $1
        """,
        evaluation_id,
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
            verifier_reward = $5,
            error_code = $6,
            error_message = $7,
            started_initializing_agent_at = $8,
            started_running_agent_at = $9,
            started_initializing_eval_at = $10,
            started_running_eval_at = $11,
            finished_or_errored_at = $12
        WHERE evaluation_run_id = $1
        """,
        evaluation_run.evaluation_run_id,
        evaluation_run.status.value,
        evaluation_run.patch,
        json.dumps([test_result.model_dump() for test_result in evaluation_run.test_results])
        if evaluation_run.test_results is not None
        else None,
        evaluation_run.verifier_reward,
        evaluation_run.error_code,
        evaluation_run.error_message,
        evaluation_run.started_initializing_agent_at,
        evaluation_run.started_running_agent_at,
        evaluation_run.started_initializing_eval_at,
        evaluation_run.started_running_eval_at,
        evaluation_run.finished_or_errored_at,
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
        EvaluationRunStatus.pending.value,
    )

    logger.debug(
        f"Created evaluation run {evaluation_run_id} for evaluation {evaluation_id} with problem name {problem_name}"
    )

    return evaluation_run_id


@db_operation
async def create_evaluation_runs(
    conn: DatabaseConnection, evaluation_id: UUID, evaluation_set_problems: List[EvaluationSetProblem]
) -> None:
    await conn.executemany(
        """
        INSERT INTO evaluation_runs (
            evaluation_run_id,
            evaluation_id,
            problem_name,
            benchmark_family,
            execution_spec,
            status,
            created_at
        ) VALUES ($1, $2, $3, $4, $5::jsonb, $6, NOW())
        """,
        [
            (
                uuid4(),
                evaluation_id,
                problem.problem_name,
                problem.benchmark_family,
                json.dumps(problem.execution_spec) if problem.execution_spec is not None else None,
                EvaluationRunStatus.pending.value,
            )
            for problem in evaluation_set_problems
        ],
    )

    logger.debug(f"Created {len(evaluation_set_problems)} evaluation runs for evaluation {evaluation_id}")


@db_operation
async def create_evaluation_run_log(
    conn: DatabaseConnection, evaluation_run_id: UUID, type: EvaluationRunLogType, logs: str
) -> None:
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
        logs.replace("\x00", ""),
    )

    num_lines = len(logs.split("\n"))
    logger.debug(
        f"Created evaluation run log for evaluation run {evaluation_run_id} with type {type}, {num_lines} line(s), {len(logs)} character(s)"
    )


@db_operation
async def check_if_evaluation_run_logs_exist(
    conn: DatabaseConnection, evaluation_run_id: UUID, type: EvaluationRunLogType
) -> bool:
    return await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM evaluation_run_logs
            WHERE evaluation_run_id = $1 AND type = $2
        )
        """,
        evaluation_run_id,
        type.value,
    )


@db_operation
async def get_evaluation_run_logs_by_id(
    conn: DatabaseConnection, evaluation_run_id: UUID, type: EvaluationRunLogType
) -> Optional[str]:
    logs = await conn.fetchval(
        """
        SELECT logs FROM evaluation_run_logs
        WHERE type = $1
        and evaluation_run_id = $2
        """,
        type,
        evaluation_run_id,
    )

    return logs
