import json
import asyncpg
import utils.logger as logger

from typing import Optional
from uuid import UUID, uuid4
from datetime import datetime
from utils.database import db_operation
from models.evaluation_run import EvaluationRun
from models.evaluation_run import EvaluationRunStatus



@db_operation
async def get_evaluation_run_by_id(conn: asyncpg.Connection, evaluation_run_id: UUID) -> Optional[EvaluationRun]:
    result = await conn.fetchrow(
        """
        SELECT *
        FROM evaluation_runs
        WHERE evaluation_run_id = $1
        """,
        evaluation_run_id
    )

    if not result:
        return None
    
    return EvaluationRun(**result)



@db_operation
async def update_evaluation_run_by_id(conn: asyncpg.Connection, evaluation_run: EvaluationRun) -> None:
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
async def create_evaluation_run(conn: asyncpg.Connection, evaluation_id: UUID, problem_name: str) -> UUID:
    evaluation_run_id = uuid4()

    await conn.execute(
        """
        INSERT INTO evaluation_runs (
            evaluation_run_id,
            evaluation_id,
            problem_name,
            status,
            created_at
        ) VALUES ($1, $2, $3, $4, $5)
        """,
        evaluation_run_id,
        evaluation_id,
        problem_name,
        EvaluationRunStatus.pending.value,
        datetime.now()
    )

    logger.debug(f"Created evaluation run {evaluation_run_id} for evaluation {evaluation_id} with problem name {problem_name}")

    return evaluation_run_id