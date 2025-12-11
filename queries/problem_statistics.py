import json

from uuid import UUID
from datetime import datetime
from pydantic import BaseModel
from typing import List, Optional
from models.problem import ProblemDifficulty
from models.evaluation_set import EvaluationSetGroup
from models.evaluation_run import EvaluationRunErrorCode
from utils.database import db_operation, DatabaseConnection
from evaluator.problem_suites.problem_suite import ProblemSuiteName
from queries.evaluation_set import get_all_evaluation_set_problems_in_latest_set_id
from evaluator.problem_suites.polyglot.polyglot_suite import POLYGLOT_PY_SUITE, POLYGLOT_JS_SUITE
from evaluator.problem_suites.swebench_verified.swebench_verified_suite import SWEBENCH_VERIFIED_SUITE

    

class ProblemStatisticsTestInfo(BaseModel):
    name: str
    category: str

    num_runs: int
    num_passed: int
    num_failed: int

    pass_rate: float

class ErroredAgentInfo(BaseModel):
    agent_id: UUID
    name: str
    version_num: int

    evaluation_id: UUID
    evaluation_run_id: UUID
    evaluation_run_started_at: datetime
    evaluation_run_errored_at: datetime
    evaluation_run_error_message: Optional[str] = None

class ProblemStatisticsErrorCodeInfo(BaseModel):
    error_code: int
    error_message: str
    recent_errored_agents: List[ErroredAgentInfo]

    num_errors: int

    def __init__(self, **data):
        data["error_message"] = EvaluationRunErrorCode(data["error_code"]).get_error_message()
        super().__init__(**data)

class ProblemStatisticsTokenInfo(BaseModel):
    model: str

    num_input_tokens: int
    num_output_tokens: int

class ProblemStatisticsFastestAgentInfo(BaseModel):
    agent_id: UUID
    name: str
    version_num: int

    evaluation_id: UUID

    evaluation_run_id: UUID
    evaluation_run_time: float



class ProblemStatistics(BaseModel):
    problem_name: str
    problem_suite_name: Optional[ProblemSuiteName] = None
    problem_difficulty: Optional[ProblemDifficulty] = None

    total_num_evaluation_runs: int = 0
    num_finished_evaluation_runs: int = 0
    num_finished_passed_evaluation_runs: int = 0
    num_finished_failed_evaluation_runs: int = 0
    num_errored_evaluation_runs: int = 0

    pass_rate: Optional[float] = None
    average_time: Optional[float] = None
    average_cost_usd: Optional[float] = None

    in_screener_1_set_group: bool
    in_screener_2_set_group: bool
    in_validator_set_group: bool

    tests: List[ProblemStatisticsTestInfo] = []
    error_code_distribution: List[ProblemStatisticsErrorCodeInfo] = []
    token_distribution: List[ProblemStatisticsTokenInfo] = []
    fastest_agents: List[ProblemStatisticsFastestAgentInfo] = []

    def __init__(self, **data):
        problem_name = data["problem_name"]

        for problem_suite in [POLYGLOT_PY_SUITE, POLYGLOT_JS_SUITE, SWEBENCH_VERIFIED_SUITE]:
            if problem_suite.has_problem_name(problem_name):
                data["problem_suite_name"] = problem_suite.name
                data["problem_difficulty"] = problem_suite.get_problem(problem_name).difficulty
                break

        if "tests" in data:
            data["tests"] = [ProblemStatisticsTestInfo(**item) for item in json.loads(data["tests"])]

        if "error_code_distribution" in data:
            data["error_code_distribution"] = [ProblemStatisticsErrorCodeInfo(**item) for item in json.loads(data["error_code_distribution"])]
        
        if "token_distribution" in data:
            data["token_distribution"] = [ProblemStatisticsTokenInfo(**item) for item in json.loads(data["token_distribution"])]

        if "fastest_agents" in data:
            data["fastest_agents"] = [ProblemStatisticsFastestAgentInfo(**item) for item in json.loads(data["fastest_agents"])]

        super().__init__(**data)



@db_operation
async def get_problem_statistics(conn: DatabaseConnection) -> List[ProblemStatistics]:
    rows = await conn.fetch(
        """
        WITH stats AS (
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
                JOIN agents a on e.agent_id = a.agent_id
            WHERE e.set_id = (SELECT MAX(set_id) FROM evaluation_sets)
                AND a.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
                AND a.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
            GROUP BY erh.problem_name
        ),
        tests_stats AS ( 
            SELECT 
                problem_name,
                jsonb_agg(
                    jsonb_build_object(
                        'name', name,
                        'category', category,
                        'num_runs', num_runs,
                        'num_passed', num_passed,
                        'num_failed', num_failed,
                        'pass_rate', pass_rate
                    )
                ) AS tests
            FROM (
                SELECT
                    er.problem_name,
                    tr->>'name' AS name,
                    tr->>'category' AS category,
                    COUNT(*) AS num_runs,
                    COUNT(*) FILTER (WHERE tr->>'status' = 'pass') AS num_passed,
                    COUNT(*) FILTER (WHERE tr->>'status' = 'fail') AS num_failed,
                    COUNT(*) FILTER (WHERE tr->>'status' = 'pass')::FLOAT / NULLIF(COUNT(*), 0) AS pass_rate
                FROM 
                    evaluation_runs er 
                    CROSS JOIN jsonb_array_elements(er.test_results) tr 
                    JOIN evaluations e ON er.evaluation_id = e.evaluation_id
                    JOIN agents a ON a.agent_id = e.agent_id
                WHERE 
                    er.status = 'finished'
                    AND e.set_id = (SELECT MAX(set_id) FROM evaluation_sets)
                    AND a.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
                    AND a.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
                GROUP BY 
                    er.problem_name, tr->>'name', tr->>'category'
            )
            GROUP BY problem_name
        ),
        error_code_distribution_stats AS (
            SELECT
                problem_name,
                json_agg(
                    jsonb_build_object(
                        'error_code', error_code,
                        'num_errors', num_errors,
                        'recent_errored_agents', recent_errored_agents
                    )
                ) AS error_code_distribution
            FROM (
                SELECT
                    problem_name,
                    error_code,
                    COUNT(*) AS num_errors,
                    json_agg(
                        jsonb_build_object(   
                            'agent_id', agent_id,
                            'name', name,
                            'version_num', version_num,
                            'evaluation_id', evaluation_id,
                            'evaluation_run_id', evaluation_run_id,
                            'evaluation_run_errored_at', evaluation_run_errored_at,
                            'evaluation_run_time', evaluation_run_time,
                            'evaluation_run_error_message', evaluation_run_error_message,
                        ) ORDER BY evaluation_run_errored_at DESC
                    ) FILTER (WHERE evaluation_run_time_rank <= 5) AS recent_errored_agents
                FROM (
                    SELECT
                        er.problem_name,
                        er.error_code,
                        a.agent_id,
                        a.name,
                        a.version_num,
                        er.evaluation_id,
                        er.evaluation_run_id,
                        er.finished_or_errored_at AS evaluation_run_errored_at,
                        EXTRACT(EPOCH FROM (er.finished_or_errored_at - er.created_at)) AS evaluation_run_time,
                        er.error_message as evaluation_run_error_message,
                        ROW_NUMBER() OVER (
                            PARTITION BY er.problem_name, er.error_code
                            ORDER BY er.finished_or_errored_at DESC
                        ) AS evaluation_run_time_rank
                    FROM evaluation_runs er
                        JOIN evaluations e ON er.evaluation_id = e.evaluation_id
                        JOIN agents a ON e.agent_id = a.agent_id
                    WHERE er.status = 'error'
                        AND e.set_id = (SELECT MAX(set_id) FROM evaluation_sets)
                        AND a.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
                        AND a.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
                )
                GROUP BY problem_name, error_code
            )
            GROUP BY problem_name
        ),
        token_distribution_stats AS (
            SELECT
                problem_name,
                json_agg(
                    jsonb_build_object('model', model, 'num_input_tokens', num_input_tokens, 'num_output_tokens', num_output_tokens)
                ) AS token_distribution
            FROM (
                SELECT
                    er.problem_name,
                    i.model,
                    COALESCE(SUM(i.num_input_tokens), 0) AS num_input_tokens,
                    COALESCE(SUM(i.num_output_tokens), 0) AS num_output_tokens
                FROM evaluation_runs er
                    JOIN inferences i ON er.evaluation_run_id = i.evaluation_run_id 
                    JOIN evaluations e ON er.evaluation_id = e.evaluation_id 
                    JOIN agents a ON e.agent_id = a.agent_id 
                WHERE e.set_id = (SELECT MAX(set_id) FROM evaluation_sets)
                    AND a.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
                    AND a.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
                GROUP BY er.problem_name, i.model
            )
            GROUP BY problem_name
        ),
        fastest_agents_stats AS (
            SELECT
                problem_name,
                json_agg(
                    jsonb_build_object(
                        'agent_id', agent_id, 
                        'name', name,
                        'version_num', version_num,
                        'evaluation_run_time', evaluation_run_time,
                        'evaluation_id', evaluation_id,
                        'evaluation_run_id', evaluation_run_id
                    ) ORDER BY evaluation_run_time ASC
                ) AS fastest_agents
            FROM (
                SELECT
                    erh.problem_name,
                    a.agent_id,
                    a.name,
                    a.version_num,
                    (ARRAY_AGG(e.evaluation_id ORDER BY EXTRACT(EPOCH FROM (erh.finished_or_errored_at - erh.created_at)) ASC))[1] AS evaluation_id,
                    (ARRAY_AGG(erh.evaluation_run_id ORDER BY EXTRACT(EPOCH FROM (erh.finished_or_errored_at - erh.created_at)) ASC))[1] AS evaluation_run_id,
                    MIN(EXTRACT(EPOCH FROM (erh.finished_or_errored_at - erh.created_at))) AS evaluation_run_time,
                    ROW_NUMBER() OVER (PARTITION BY erh.problem_name ORDER BY MIN(EXTRACT(EPOCH FROM (erh.finished_or_errored_at - erh.created_at))) ASC) AS evaluation_run_time_rank
                FROM evaluation_runs_hydrated erh
                    JOIN evaluations e ON erh.evaluation_id = e.evaluation_id
                    JOIN agents a ON e.agent_id = a.agent_id
                WHERE erh.status = 'finished'
                    AND erh.solved
                    AND e.set_id = (SELECT MAX(set_id) FROM evaluation_sets)
                    AND a.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
                    AND a.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
                GROUP BY erh.problem_name, a.agent_id
            )
            WHERE evaluation_run_time_rank <= 5
            GROUP BY problem_name
        )
        SELECT 
            s.*,
            COALESCE(ts.tests, '[]') AS tests,
            COALESCE(ecds.error_code_distribution, '[]') AS error_code_distribution,
            COALESCE(tdss.token_distribution, '[]') AS token_distribution,
            COALESCE(fas.fastest_agents, '[]') AS fastest_agents
        FROM stats s
        LEFT JOIN tests_stats ts ON s.problem_name = ts.problem_name
        LEFT JOIN error_code_distribution_stats ecds ON s.problem_name = ecds.problem_name
        LEFT JOIN token_distribution_stats tdss ON s.problem_name = tdss.problem_name
        LEFT JOIN fastest_agents_stats fas ON s.problem_name = fas.problem_name
        """
    )

    problem_stats = [ProblemStatistics(**row) for row in rows]

    evaluation_set_problems = await get_all_evaluation_set_problems_in_latest_set_id()
    for evaluation_set_problem in evaluation_set_problems:
        if not any(problem_stat.problem_name == evaluation_set_problem.problem_name for problem_stat in problem_stats):
            problem_stats.append(ProblemStatistics(
                problem_name=evaluation_set_problem.problem_name,

                in_screener_1_set_group=any(_evaluation_set_problem.problem_name == evaluation_set_problem.problem_name and _evaluation_set_problem.set_group == EvaluationSetGroup.screener_1 for _evaluation_set_problem in evaluation_set_problems),
                in_screener_2_set_group=any(_evaluation_set_problem.problem_name == evaluation_set_problem.problem_name and _evaluation_set_problem.set_group == EvaluationSetGroup.screener_2 for _evaluation_set_problem in evaluation_set_problems),
                in_validator_set_group=any(_evaluation_set_problem.problem_name == evaluation_set_problem.problem_name and _evaluation_set_problem.set_group == EvaluationSetGroup.validator for _evaluation_set_problem in evaluation_set_problems)
            ))

    return problem_stats