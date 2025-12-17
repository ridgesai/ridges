import api.config as config

from datetime import datetime
from pydantic import BaseModel
from typing import Dict, Optional
from models.evaluation_set import EvaluationSetGroup
from utils.database import db_operation, DatabaseConnection



@db_operation
async def top_score(conn: DatabaseConnection) -> Optional[float]:
    return await conn.fetchval("""
        SELECT MAX(final_score) FROM agent_scores 
        WHERE set_id = (SELECT MAX(set_id) FROM evaluation_sets)
        AND agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
    """)

@db_operation
async def agents_created_24_hrs(conn: DatabaseConnection) -> int:
    return await conn.fetchval("""
        SELECT COUNT(*) FROM agents 
        WHERE created_at >= NOW() - INTERVAL '24 hours'
        AND miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
        AND agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
        AND agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
    """)

@db_operation
async def score_improvement_24_hrs(conn: DatabaseConnection) -> float:
    return await conn.fetchval(
        """
        WITH score_data AS (
            SELECT
                MAX(final_score) as max_score,
                MAX(final_score) FILTER (WHERE created_at <= NOW() - INTERVAL '24 hours') as max_score_24_hrs_ago
            FROM agent_scores
            WHERE set_id = (SELECT MAX(set_id) FROM evaluation_sets)
            AND agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
        )
        SELECT
            COALESCE(max_score - max_score_24_hrs_ago, 0)
        FROM score_data
        """
    )

class TopScoreOverTime(BaseModel):
    hour: datetime
    top_score: float

@db_operation
async def get_top_scores_over_time(conn: DatabaseConnection) -> list[TopScoreOverTime]:
    query = """
        WITH
        max_set AS (
            SELECT MAX(set_id) as set_id FROM evaluation_sets
        ),
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
                    AND agent_scores.set_id = (SELECT set_id FROM max_set)
                    AND a.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
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
                AND agent_scores.set_id = (SELECT set_id FROM max_set)
                AND a.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
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
    rows = await conn.fetch(query)
    return [TopScoreOverTime(**row) for row in rows]



class PerfectlySolvedOverTime(BaseModel):
    hour: datetime
    polyglot_py: int
    polyglot_js: int
    swebench: int

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
            problem_groups AS (
                SELECT
                    MIN(erh.created_at) as first_perfectly_solved_at,
                    CASE
                        WHEN erh.problem_name LIKE '%-py' THEN 'polyglot_py'
                        WHEN erh.problem_name LIKE '%-js' THEN 'polyglot_js'
                        ELSE 'swebench'
                    END AS problem_group
                FROM evaluation_runs_hydrated erh
                    JOIN evaluations e ON erh.evaluation_id = e.evaluation_id
                    JOIN agents a ON e.agent_id = a.agent_id
                WHERE erh.created_at >= TIMESTAMP WITH TIME ZONE '2025-11-27 15:30:00.000 -0500' -- Problem Set 6
                    AND erh.status = 'finished'
                    AND a.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
                    AND e.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
                    AND e.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
                GROUP BY erh.problem_name
                HAVING COUNT(*) FILTER (WHERE erh.solved = true)::float / COUNT(*) >= 0.90
            )
        SELECT
            ts.hour,
            COUNT(*) FILTER (WHERE pg.problem_group = 'polyglot_py') as polyglot_py,
            COUNT(*) FILTER (WHERE pg.problem_group = 'polyglot_js') as polyglot_js,
            COUNT(*) FILTER (WHERE pg.problem_group = 'swebench') as swebench
        FROM time_series ts
        LEFT JOIN problem_groups pg ON pg.first_perfectly_solved_at <= ts.hour
        GROUP BY ts.hour
        ORDER BY ts.hour ASC;
    """
    rows = await conn.fetch(query)
    return [PerfectlySolvedOverTime(**row) for row in rows]



# NOTE: None is returned if there are no successful evaluations for a given
#       evaluation set group. 
@db_operation
async def get_average_score_per_evaluation_set_group(conn: DatabaseConnection) -> Dict[EvaluationSetGroup, Optional[float]]:
    rows = await conn.fetch(
        """
        SELECT
            eh.evaluation_set_group as validator_type,
            AVG(eh.score) as average_score
        FROM evaluations_hydrated eh
            JOIN agents a on a.agent_id = eh.agent_id 
        WHERE eh.status = 'success'
            AND eh.set_id = (SELECT MAX(set_id) FROM evaluation_sets)
            AND a.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
            AND eh.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
            AND eh.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
        GROUP BY validator_type
        """
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
async def get_average_wait_time_per_evaluation_set_group(conn: DatabaseConnection) -> Dict[EvaluationSetGroup, Optional[float]]:
    result = {}

    result[EvaluationSetGroup.screener_1] = await conn.fetchval(
        f"""
        SELECT 
            AVG(EXTRACT(EPOCH FROM (e.finished_at - a.created_at))) AS average_wait_time
        FROM evaluations_hydrated e
            JOIN agents a ON e.agent_id = a.agent_id
        WHERE e.status = 'success'
            AND e.evaluation_set_group = '{EvaluationSetGroup.screener_1.value}'::EvaluationSetGroup
            AND e.set_id = (SELECT MAX(set_id) FROM evaluation_sets)
            AND a.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
            AND a.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
            AND a.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
            AND e.finished_at >= NOW() - INTERVAL '6 hours'
        """
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
            AND sc1_e.set_id = (SELECT MAX(set_id) FROM evaluation_sets)
            AND sc2_e.set_id = (SELECT MAX(set_id) FROM evaluation_sets)
            AND a.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
            AND a.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
            AND a.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
            AND sc2_e.finished_at >= NOW() - INTERVAL '6 hours'
        """
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
                    AND v_e2.set_id = (SELECT MAX(set_id) FROM evaluation_sets)
                GROUP BY v_e2.agent_id
            ) v_e ON sc2_e.agent_id = v_e.agent_id
            JOIN agents a ON sc2_e.agent_id = a.agent_id
        WHERE sc2_e.status = 'success'
            AND sc2_e.evaluation_set_group = '{EvaluationSetGroup.screener_2.value}'::EvaluationSetGroup
            AND sc2_e.set_id = (SELECT MAX(set_id) FROM evaluation_sets)
            AND v_e.validator_count = {config.NUM_EVALS_PER_AGENT}
            AND a.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
            AND a.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
            AND a.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
            AND v_e.finished_at >= NOW() - INTERVAL '6 hours'
        """
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
