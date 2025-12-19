import json
import api.config as config

from datetime import datetime
from pydantic import BaseModel
from typing import List, Dict, Optional
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



class ValidatorStatsErrorCodeInfo(BaseModel):
    error_code: int
    count: int

class ValidatorStats(BaseModel):
    validator_hotkey: str
    
    num_evals: int
    
    num_eval_runs: int
    num_success_eval_runs: int
    num_pass_eval_runs: int
    num_fail_eval_runs: int
    num_error_eval_runs: int
    error_code_distribution: List[ValidatorStatsErrorCodeInfo]
    
    num_inferences: int
    num_embeddings: int
    
    runtime_min: Optional[float] = None
    runtime_q1: Optional[float] = None
    runtime_median: Optional[float] = None
    runtime_q3: Optional[float] = None
    runtime_max: Optional[float] = None
    runtime_mean: Optional[float] = None
    
    score_min: Optional[float] = None
    score_q1: Optional[float] = None
    score_median: Optional[float] = None
    score_q3: Optional[float] = None
    score_max: Optional[float] = None
    score_mean: Optional[float] = None

    def __init__(self, **data):
        if "error_code_distribution" in data:
            data["error_code_distribution"] = [ValidatorStatsErrorCodeInfo(**item) for item in json.loads(data["error_code_distribution"])]
        
        super().__init__(**data)

@db_operation
async def get_validator_stats(conn: DatabaseConnection) -> int:
    rows = await conn.fetch(
        """
        WITH current_set AS (
            SELECT MAX(set_id) as set_id FROM evaluation_sets
        ),
        validator_eval_runs AS (
            SELECT 
                e.validator_hotkey,
                erh.evaluation_id,
                erh.evaluation_run_id,
                erh.status,
                erh.solved,
                erh.error_code,
                erh.started_initializing_agent_at,
                erh.finished_or_errored_at,
                EXTRACT(EPOCH FROM (erh.finished_or_errored_at - erh.started_initializing_agent_at)) as runtime_seconds,
                COUNT(DISTINCT i.inference_id) as num_inferences,
                COUNT(DISTINCT em.embedding_id) as num_embeddings
            FROM evaluations e
                JOIN evaluation_runs_hydrated erh ON e.evaluation_id = erh.evaluation_id
                JOIN agents a ON e.agent_id = a.agent_id
                LEFT JOIN inferences i ON erh.evaluation_run_id = i.evaluation_run_id
                LEFT JOIN embeddings em ON erh.evaluation_run_id = em.evaluation_run_id
            WHERE e.set_id = (SELECT set_id FROM current_set)
                AND e.evaluation_set_group = 'validator'::EvaluationSetGroup
                AND a.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
                AND e.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
                AND e.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
            GROUP BY 
                e.validator_hotkey,
                erh.evaluation_run_id,
                erh.evaluation_id,
                erh.status,
                erh.error_code,
                erh.finished_or_errored_at,
                erh.started_initializing_agent_at,
                erh.solved
        ),
        validator_stats AS (
            SELECT 
                validator_hotkey,
                COUNT(DISTINCT evaluation_id) as num_evals,
                COUNT(evaluation_run_id) as num_eval_runs,
                COUNT(*) FILTER (WHERE status = 'finished') as num_success_eval_runs,
                COUNT(*) FILTER (WHERE status = 'finished' AND solved) as num_pass_eval_runs,
                COUNT(*) FILTER (WHERE status = 'finished' AND NOT solved) as num_fail_eval_runs,
                COUNT(*) FILTER (WHERE status = 'error') as num_error_eval_runs,
                COALESCE(SUM(num_inferences), 0) as num_inferences,
                COALESCE(SUM(num_embeddings), 0) as num_embeddings
            FROM validator_eval_runs
            GROUP BY validator_hotkey
        ),
        error_code_distribution AS (
            SELECT 
                validator_hotkey,
                COALESCE(
                    json_agg(
                        jsonb_build_object('error_code', error_code, 'count', error_count)
                    ),
                    '[]'::json
                ) as error_code_distribution
            FROM (
                SELECT 
                    validator_hotkey,
                    error_code,
                    COUNT(*) as error_count
                FROM validator_eval_runs
                WHERE status = 'error' AND error_code IS NOT NULL
                GROUP BY validator_hotkey, error_code
            )
            GROUP BY validator_hotkey
        ),
        runtime_quartiles AS (
            SELECT 
                validator_hotkey,
                PERCENTILE_CONT(0.00) WITHIN GROUP (ORDER BY runtime_seconds) as runtime_min,
                PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY runtime_seconds) as runtime_q1,
                PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY runtime_seconds) as runtime_median,
                PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY runtime_seconds) as runtime_q3,
                PERCENTILE_CONT(1.00) WITHIN GROUP (ORDER BY runtime_seconds) as runtime_max,
                AVG(runtime_seconds) as runtime_mean
            FROM validator_eval_runs
            WHERE runtime_seconds IS NOT NULL
            GROUP BY validator_hotkey
        ),
        score_quartiles AS (
            SELECT 
                validator_hotkey,
                PERCENTILE_CONT(0.00) WITHIN GROUP (ORDER BY score) as score_min,
                PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY score) as score_q1,
                PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY score) as score_median,
                PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY score) as score_q3,
                PERCENTILE_CONT(1.00) WITHIN GROUP (ORDER BY score) as score_max,
                AVG(score) as score_mean
            FROM (
                SELECT 
                    validator_hotkey,
                    evaluation_id,
                    COUNT(*) FILTER (WHERE solved = true)::float / NULLIF(COUNT(*) FILTER (WHERE status = 'finished'), 0) AS score
                FROM validator_eval_runs
                WHERE status IN ('finished', 'error')
                GROUP BY validator_hotkey, evaluation_id
                HAVING COUNT(*) FILTER (WHERE status = 'finished') > 0
            )
            GROUP BY validator_hotkey
        )
        SELECT 
            vs.validator_hotkey,
            vs.num_evals,
            vs.num_eval_runs,
            vs.num_success_eval_runs,
            vs.num_pass_eval_runs,
            vs.num_fail_eval_runs,
            vs.num_error_eval_runs,
            ecd.error_code_distribution,
            vs.num_inferences,
            vs.num_embeddings,
            rq.runtime_min,
            rq.runtime_q1,
            rq.runtime_median,
            rq.runtime_q3,
            rq.runtime_max,
            rq.runtime_mean,
            sq.score_min,
            sq.score_q1,
            sq.score_median,
            sq.score_q3,
            sq.score_max,
            sq.score_mean
        FROM validator_stats vs
            LEFT JOIN error_code_distribution ecd ON vs.validator_hotkey = ecd.validator_hotkey
            LEFT JOIN runtime_quartiles rq ON vs.validator_hotkey = rq.validator_hotkey
            LEFT JOIN score_quartiles sq ON vs.validator_hotkey = sq.validator_hotkey
        ORDER BY vs.validator_hotkey;
        """
    )
    return [ValidatorStats(**row) for row in rows]
