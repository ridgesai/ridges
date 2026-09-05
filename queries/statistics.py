from datetime import datetime
from typing import Dict, Optional

from pydantic import BaseModel, Field

from models.evaluation_set import EvaluationSetGroup
from utils.database import DatabaseConnection, db_operation


class TopScoreOverTime(BaseModel):
    hour: datetime
    top_score: float


@db_operation
async def get_top_scores_over_time(conn: DatabaseConnection, set_id: int) -> list[TopScoreOverTime]:
    query = """
        WITH
        time_series AS (
            SELECT
            generate_series(
                (
                SELECT
                    MIN(DATE_TRUNC('hour', agent_scores.created_at))
                FROM
                    agent_scores
                JOIN
                    agents a ON agent_scores.agent_id = a.agent_id
                WHERE
                    agent_scores.final_score IS NOT NULL
                    AND agent_scores.set_id = $1
                    AND (a.set_id IS NULL OR a.set_id = agent_scores.set_id)
                    AND NOT EXISTS (
                        SELECT 1 FROM banned_coldkeys bc
                        WHERE bc.miner_coldkey = a.miner_coldkey
                    )
                    AND a.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
                    AND a.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
                ),
                DATE_TRUNC('hour', NOW()),
                '1 hour'::interval
            ) as hour
        )
        SELECT
        ts.hour,
        COALESCE(
            (
            SELECT
                MAX(agent_scores.final_score)
            FROM
                agent_scores
            JOIN
                agents a ON agent_scores.agent_id = a.agent_id
            WHERE
                agent_scores.final_score IS NOT NULL
                AND agent_scores.created_at <= ts.hour
                AND agent_scores.set_id = $1
                AND (a.set_id IS NULL OR a.set_id = agent_scores.set_id)
                AND NOT EXISTS (
                    SELECT 1 FROM banned_coldkeys bc
                    WHERE bc.miner_coldkey = a.miner_coldkey
                )
                AND a.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
                AND a.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
            ),
            0
        ) as top_score
        FROM
        time_series ts
        ORDER BY
        ts.hour
    """
    rows = await conn.fetch(query, set_id)
    return [TopScoreOverTime(**row) for row in rows]


class PerfectlySolvedOverTime(BaseModel):
    hour: datetime
    total_solved: int
    by_family: dict[str, int] = Field(default_factory=dict)


@db_operation
async def get_perfectly_solved_over_time(conn: DatabaseConnection) -> list[PerfectlySolvedOverTime]:
    query = """
        WITH
            time_series AS (
                SELECT generate_series(
                    TIMESTAMP WITH TIME ZONE '2025-11-27 15:30:00.000 -0500', -- Problem Set 6
                    DATE_TRUNC('hour', NOW()),
                    '6 hours'::interval
                ) as hour
            ),
            perfectly_solved_problems AS (
                SELECT
                    erh.benchmark_family,
                    erh.problem_name,
                    MIN(erh.created_at) as first_perfectly_solved_at
                FROM evaluation_runs_hydrated erh
                    JOIN evaluations e ON erh.evaluation_id = e.evaluation_id
                    JOIN agents a ON e.agent_id = a.agent_id
                WHERE erh.created_at >= TIMESTAMP WITH TIME ZONE '2025-11-27 15:30:00.000 -0500' -- Problem Set 6
                    AND erh.status = 'finished'
                    AND erh.benchmark_family IS NOT NULL
                    AND erh.benchmark_family <> ''
                    AND erh.benchmark_family <> 'custom'
                    AND NOT EXISTS (
                        SELECT 1 FROM banned_coldkeys bc
                        WHERE bc.miner_coldkey = a.miner_coldkey
                    )
                    AND e.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
                    AND e.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
                GROUP BY erh.benchmark_family, erh.problem_name
                HAVING COUNT(*) FILTER (WHERE erh.solved = true)::float / COUNT(*) >= 0.90
            )
        SELECT
            ts.hour,
            psp.benchmark_family,
            COUNT(psp.problem_name)::int as solved_count
        FROM time_series ts
        LEFT JOIN perfectly_solved_problems psp ON psp.first_perfectly_solved_at <= ts.hour
        GROUP BY ts.hour, psp.benchmark_family
        ORDER BY ts.hour ASC, psp.benchmark_family ASC NULLS LAST;
    """
    rows = await conn.fetch(query)

    results: list[PerfectlySolvedOverTime] = []
    current_point: PerfectlySolvedOverTime | None = None

    for row in rows:
        hour = row["hour"]
        if current_point is None or current_point.hour != hour:
            if current_point is not None:
                results.append(current_point)
            current_point = PerfectlySolvedOverTime(hour=hour, total_solved=0, by_family={})

        benchmark_family = row["benchmark_family"]
        solved_count = int(row["solved_count"] or 0)

        if benchmark_family is None or solved_count <= 0:
            continue

        current_point.by_family[benchmark_family] = solved_count
        current_point.total_solved += solved_count

    if current_point is not None:
        results.append(current_point)

    return results


# NOTE: None is returned if there are no successful evaluations for a given
#       evaluation set group.
@db_operation
async def get_average_score_per_evaluation_set_group(
    conn: DatabaseConnection,
    set_id: int,
) -> Dict[EvaluationSetGroup, Optional[float]]:
    rows = await conn.fetch(
        """
        SELECT
            eh.evaluation_set_group as validator_type,
            AVG(eh.score) as average_score
        FROM evaluations_hydrated eh
            JOIN agents a on a.agent_id = eh.agent_id 
        WHERE eh.status = 'success'
            AND eh.set_id = $1
            AND (a.set_id IS NULL OR a.set_id = eh.set_id)
            AND NOT EXISTS (
                SELECT 1 FROM banned_coldkeys bc
                WHERE bc.miner_coldkey = a.miner_coldkey
            )
            AND eh.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
            AND eh.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
        GROUP BY validator_type
        """,
        set_id,
    )

    result = {EvaluationSetGroup(row["validator_type"]): float(row["average_score"]) for row in rows}

    if EvaluationSetGroup.screener_1 not in result:
        result[EvaluationSetGroup.screener_1] = None
    if EvaluationSetGroup.screener_2 not in result:
        result[EvaluationSetGroup.screener_2] = None
    if EvaluationSetGroup.validator not in result:
        result[EvaluationSetGroup.validator] = None

    return result


# NOTE: None is returned if there are no successful evaluations for a given
#       evaluation set group.
@db_operation
async def get_average_wait_time_per_evaluation_set_group(
    conn: DatabaseConnection,
    set_id: int,
    required_validator_count: int | None,
) -> Dict[EvaluationSetGroup, Optional[float]]:
    result = {}

    result[EvaluationSetGroup.screener_1] = await conn.fetchval(
        f"""
        SELECT 
            AVG(EXTRACT(EPOCH FROM (e.finished_at - a.created_at))) AS average_wait_time
        FROM evaluations_hydrated e
            JOIN agents a ON e.agent_id = a.agent_id
        WHERE e.status = 'success'
            AND e.evaluation_set_group = '{EvaluationSetGroup.screener_1.value}'::EvaluationSetGroup
            AND e.set_id = $1
            AND (a.set_id IS NULL OR a.set_id = e.set_id)
            AND NOT EXISTS (
                SELECT 1 FROM banned_coldkeys bc
                WHERE bc.miner_coldkey = a.miner_coldkey
            )
            AND a.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
            AND a.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
            AND e.finished_at >= NOW() - INTERVAL '6 hours'
        """,
        set_id,
    )

    result[EvaluationSetGroup.screener_2] = await conn.fetchval(
        f"""
        SELECT 
            AVG(EXTRACT(EPOCH FROM (sc2_e.finished_at - sc1_e.finished_at))) AS average_wait_time
        FROM evaluations_hydrated sc1_e
            JOIN evaluations_hydrated sc2_e ON sc1_e.agent_id = sc2_e.agent_id
            JOIN agents a ON sc1_e.agent_id = a.agent_id
        WHERE sc1_e.status = 'success' AND sc2_e.status = 'success'
            AND sc1_e.evaluation_set_group = '{EvaluationSetGroup.screener_1.value}'::EvaluationSetGroup
            AND sc2_e.evaluation_set_group = '{EvaluationSetGroup.screener_2.value}'::EvaluationSetGroup
            AND sc1_e.set_id = $1
            AND sc2_e.set_id = $1
            AND (a.set_id IS NULL OR a.set_id = sc1_e.set_id)
            AND NOT EXISTS (
                SELECT 1 FROM banned_coldkeys bc
                WHERE bc.miner_coldkey = a.miner_coldkey
            )
            AND a.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
            AND a.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
            AND sc2_e.finished_at >= NOW() - INTERVAL '6 hours'
        """,
        set_id,
    )

    result[EvaluationSetGroup.validator] = await conn.fetchval(
        f"""
        SELECT 
            AVG(EXTRACT(EPOCH FROM (v_e.finished_at - sc2_e.finished_at))) AS average_wait_time
        FROM evaluations_hydrated sc2_e
            JOIN (
                SELECT
                    v_e2.agent_id,
                    MAX(v_e2.finished_at) AS finished_at,
                    COUNT(DISTINCT v_e2.validator_hotkey) AS validator_count
                    FROM evaluations_hydrated v_e2
                    WHERE v_e2.status = 'success'
                    AND v_e2.evaluation_set_group = '{EvaluationSetGroup.validator.value}'::EvaluationSetGroup
                    AND v_e2.set_id = $1
                GROUP BY v_e2.agent_id
            ) v_e ON sc2_e.agent_id = v_e.agent_id
            JOIN agents a ON sc2_e.agent_id = a.agent_id
        WHERE sc2_e.status = 'success'
            AND sc2_e.evaluation_set_group = '{EvaluationSetGroup.screener_2.value}'::EvaluationSetGroup
            AND sc2_e.set_id = $1
            AND (a.set_id IS NULL OR a.set_id = sc2_e.set_id)
            AND v_e.validator_count = $2
            AND NOT EXISTS (
                SELECT 1 FROM banned_coldkeys bc
                WHERE bc.miner_coldkey = a.miner_coldkey
            )
            AND a.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
            AND a.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
            AND v_e.finished_at >= NOW() - INTERVAL '6 hours'
        """,
        set_id,
        required_validator_count,
    )

    return result


class ProblemSetCreationTime(BaseModel):
    set_id: int
    created_at: datetime


@db_operation
async def get_problem_set_creation_times(conn: DatabaseConnection) -> list[ProblemSetCreationTime]:
    rows = await conn.fetch(
        """
        SELECT DISTINCT set_id, created_at
        FROM evaluation_sets es
        WHERE set_id >= 6 ORDER BY set_id ASC
        """
    )

    return [ProblemSetCreationTime(**row) for row in rows]
