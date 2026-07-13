import json
import logging
from typing import List, Optional
from uuid import UUID, uuid4

import asyncpg

from models.evaluation_run import EvaluationRun, EvaluationRunLogType, EvaluationRunStatus
from models.evaluation_set import EvaluationSetProblem
from queries._row_parsing import parse_jsonb_fields
from utils.database import DatabaseConnection, db_operation

logger = logging.getLogger(__name__)


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


def _parse_metrics_row(row: asyncpg.Record) -> dict:
    return {
        "run_time_seconds": row["run_time_seconds"],
        "problem_total_runs": row["problem_total_runs"],
        "problem_average_time_seconds": row["problem_average_time_seconds"],
        "problem_average_cost_usd": row["problem_average_cost_usd"],
    }


async def _get_evaluation_run_metrics_by_ids(
    conn: DatabaseConnection, evaluation_run_ids: List[UUID]
) -> dict[UUID, dict]:
    if not evaluation_run_ids:
        return {}

    rows = await conn.fetch(
        """
        WITH target_runs AS (
            SELECT
                er.evaluation_run_id,
                er.problem_name,
                e.set_id,
                CASE
                    WHEN er.started_initializing_agent_at IS NOT NULL
                     AND er.finished_or_errored_at IS NOT NULL
                    THEN EXTRACT(EPOCH FROM (er.finished_or_errored_at - er.started_initializing_agent_at))
                    ELSE NULL
                END AS run_time_seconds
            FROM evaluation_runs er
            JOIN evaluations e ON e.evaluation_id = er.evaluation_id
            WHERE er.evaluation_run_id = ANY($1::uuid[])
        ),
        distinct_pairs AS (
            SELECT
                DISTINCT set_id,
                problem_name
            FROM
                target_runs
        ),
        problem_aggregates AS (
            SELECT
                dp.set_id,
                dp.problem_name,
                agg.problem_total_runs,
                agg.problem_average_time_seconds,
                agg.problem_average_cost_usd
            FROM
                distinct_pairs dp,
                LATERAL (
                    SELECT
                        COUNT(*) AS problem_total_runs,
                        AVG(
                            CASE
                                WHEN er2.started_initializing_agent_at IS NOT NULL
                                AND er2.finished_or_errored_at IS NOT NULL THEN EXTRACT(
                                    EPOCH
                                    FROM
                                        (
                                            er2.finished_or_errored_at - er2.started_initializing_agent_at
                                        )
                                )
                                ELSE NULL
                            END
                        ) AS problem_average_time_seconds,
                        AVG(COALESCE(er2.cost_usd, 0)) AS problem_average_cost_usd
                    FROM
                        evaluations e2
                        JOIN evaluation_runs er2 ON er2.evaluation_id = e2.evaluation_id
                        AND er2.problem_name = dp.problem_name
                        JOIN agents a ON a.agent_id = e2.agent_id
                        LEFT JOIN banned_coldkeys bc ON bc.miner_coldkey = a.miner_coldkey
                        LEFT JOIN unapproved_agent_ids uai ON uai.agent_id = a.agent_id
                        LEFT JOIN benchmark_agent_ids bai ON bai.agent_id = a.agent_id
                    WHERE
                        e2.set_id = dp.set_id
                        AND bc.miner_coldkey IS NULL
                        AND uai.agent_id IS NULL
                        AND bai.agent_id IS NULL
                ) agg
        )
        SELECT
            tr.evaluation_run_id,
            tr.run_time_seconds,
            COALESCE(pa.problem_total_runs, 0) AS problem_total_runs,
            pa.problem_average_time_seconds,
            pa.problem_average_cost_usd
        FROM target_runs tr
        LEFT JOIN problem_aggregates pa
            ON pa.set_id = tr.set_id
           AND pa.problem_name = tr.problem_name
        """,
        evaluation_run_ids,
    )

    return {row["evaluation_run_id"]: _parse_metrics_row(row) for row in rows}


@db_operation
async def get_evaluation_run_metrics_by_ids(
    conn: DatabaseConnection, evaluation_run_ids: List[UUID]
) -> dict[UUID, dict]:
    return await _get_evaluation_run_metrics_by_ids(conn, evaluation_run_ids)


@db_operation
async def get_evaluation_run_metrics_by_id(conn: DatabaseConnection, evaluation_run_id: UUID) -> Optional[dict]:
    metrics_by_id = await _get_evaluation_run_metrics_by_ids(conn, [evaluation_run_id])
    return metrics_by_id.get(evaluation_run_id)


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
            finished_or_errored_at = $12,
            cost_usd = $13
        WHERE evaluation_run_id = $1
        """,
        evaluation_run.evaluation_run_id,
        evaluation_run.status.value,
        evaluation_run.patch,
        json.dumps(
            [
                test_result.model_dump(exclude={"test_alias"}, exclude_none=True)
                for test_result in evaluation_run.test_results
            ]
        )
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
        evaluation_run.cost_usd,
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
