from pydantic import BaseModel
from datetime import datetime
from typing import List, Optional
from utils.database import db_operation, DatabaseConnection
from evaluator.datasets.problem_statistics import ProblemStatisticsProblemSuite, ProblemStatisticsProblemDifficulty, get_problem_statistics_by_problem_name



@db_operation
async def top_score(conn: DatabaseConnection) -> float:
    return await conn.fetchval("SELECT MAX(final_score) FROM agent_scores WHERE set_id = (SELECT MAX(set_id) FROM evaluation_sets)")

@db_operation
async def agents_created_24_hrs(conn: DatabaseConnection) -> int:
    return await conn.fetchval("SELECT COUNT(*) FROM agents WHERE created_at >= NOW() - INTERVAL '24 hours'")

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











class ProblemStatistics(BaseModel):
    problem_name: str
    problem_suite: ProblemStatisticsProblemSuite
    problem_difficulty: Optional[ProblemStatisticsProblemDifficulty]
    total_num_evaluation_runs: int
    num_finished_evaluation_runs: int
    num_finished_passed_evaluation_runs: int
    num_finished_failed_evaluation_runs: int
    num_errored_evaluation_runs: int
    pass_rate: Optional[float]
    average_time: Optional[float]
    average_cost_usd: Optional[float]
    in_screener_1_set_group: bool
    in_screener_2_set_group: bool
    in_validator_set_group: bool
    def __init__(self, **data):
        problem_suite, problem_difficulty = get_problem_statistics_by_problem_name(data["problem_name"])
        data["problem_suite"] = problem_suite
        data["problem_difficulty"] = problem_difficulty
        super().__init__(**data)

@db_operation
async def get_problem_statistics(conn: DatabaseConnection) -> List[ProblemStatistics]:
    rows = await conn.fetch(
        """
        SELECT
            erh.problem_name,
            COUNT(*) AS total_num_evaluation_runs,
            COUNT(*) FILTER (WHERE erh.status = 'finished') AS num_finished_evaluation_runs,
            COUNT(*) FILTER (WHERE erh.status = 'finished' AND erh.solved) AS num_finished_passed_evaluation_runs,
            COUNT(*) FILTER (WHERE erh.status = 'finished' AND NOT erh.solved) AS num_finished_failed_evaluation_runs,
            COUNT(*) FILTER (WHERE erh.status = 'error') AS num_errored_evaluation_runs,
            COUNT(*) FILTER (WHERE erh.status = 'finished' AND erh.solved)::FLOAT / NULLIF(COUNT(*) FILTER (WHERE erh.status = 'finished'), 0) AS pass_rate,
            AVG(EXTRACT(EPOCH FROM (erh.finished_or_errored_at - erh.started_initializing_agent_at))) AS average_time,
            AVG(erwc.total_cost_usd) AS average_cost_usd,
            EXISTS(
                SELECT 1 FROM evaluation_sets es1 
                WHERE es1.problem_name = erh.problem_name 
                AND es1.set_group = 'screener_1' 
                AND es1.set_id = (SELECT MAX(set_id) FROM evaluation_sets)
            ) AS in_screener_1_set_group,
            EXISTS(
                SELECT 1 FROM evaluation_sets es2 
                WHERE es2.problem_name = erh.problem_name 
                AND es2.set_group = 'screener_2' 
                AND es2.set_id = (SELECT MAX(set_id) FROM evaluation_sets)
            ) AS in_screener_2_set_group,
            EXISTS(
                SELECT 1 FROM evaluation_sets es3 
                WHERE es3.problem_name = erh.problem_name 
                AND es3.set_group = 'validator' 
                AND es3.set_id = (SELECT MAX(set_id) FROM evaluation_sets)
            ) AS in_validator_set_group
        FROM evaluation_runs_hydrated erh
        JOIN evaluation_runs_with_cost erwc ON erh.evaluation_run_id = erwc.evaluation_run_id
        JOIN evaluations e ON erh.evaluation_id = e.evaluation_id
        WHERE e.set_id = (SELECT MAX(set_id) FROM evaluation_sets)
        GROUP BY erh.problem_name
        """
    )

    return [ProblemStatistics(**row) for row in rows]
