import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, TypeAdapter
from typing import List, Optional
import json
from models.evaluation_run import EvaluationRunErrorCode
from models.problem import ProblemDifficulty
from models.evaluation_set import EvaluationSetGroup
from utils.database import db_operation, DatabaseConnection
from evaluator.problem_suites.problem_suite import ProblemSuiteName
from queries.evaluation_set import get_all_evaluation_set_problems_in_latest_set_id, get_latest_set_id, get_latest_set_created_at
from evaluator.problem_suites.polyglot.polyglot_suite import POLYGLOT_PY_SUITE, POLYGLOT_JS_SUITE
from evaluator.problem_suites.swebench_verified.swebench_verified_suite import SWEBENCH_VERIFIED_SUITE



class ErrorCodeInfo(BaseModel):
    error_code: str
    description: str = ""
    num_errors: int

class TokenInfo(BaseModel):
    model: str
    num_tokens: int

class TopAgentInfo(BaseModel):
    name: str
    agent_id: UUID
    version: int
    run_time: float

class ProblemInfo(BaseModel):
    problem_name: str
    problem_suite_name: Optional[ProblemSuiteName]
    problem_difficulty: Optional[ProblemDifficulty]
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
    error_code_distribution: list[ErrorCodeInfo] = []
    token_distribution: list[TokenInfo] = []
    top_agents_run_on_problem: list[TopAgentInfo] = []

    def __init__(self, **data):
        problem_name = data["problem_name"]

        problem_suite_name = None
        problem_difficulty = None

        for problem_suite in [POLYGLOT_PY_SUITE, POLYGLOT_JS_SUITE, SWEBENCH_VERIFIED_SUITE]:
            if problem_suite.has_problem_name(problem_name):
                problem_suite_name = problem_suite.name
                problem_difficulty = problem_suite.get_problem(problem_name).difficulty
                break    

        data["problem_suite_name"] = problem_suite_name
        data["problem_difficulty"] = problem_difficulty
        
        # string means it's coming from the database as a json
        if isinstance(data.get("error_code_distribution"), str):
            data["error_code_distribution"] = TypeAdapter(list[ErrorCodeInfo]).validate_json(data["error_code_distribution"])
        for error_code_info in data["error_code_distribution"]:
            error_code_info.description = EvaluationRunErrorCode(int(error_code_info.error_code)).get_error_message()
        
        if isinstance(data.get("token_distribution"), str):
            data["token_distribution"] = TypeAdapter(list[TokenInfo]).validate_json(data["token_distribution"])

        if isinstance(data.get("top_agents_run_on_problem"), str):
            data["top_agents_run_on_problem"] = TypeAdapter(list[TopAgentInfo]).validate_json(data["top_agents_run_on_problem"])

        super().__init__(**data)

class ProblemStatistics(BaseModel):
    problem_set_id: int
    problem_set_created_at: datetime.datetime
    problems: list[ProblemInfo]



@db_operation
async def get_problem_statistics(conn: DatabaseConnection) -> list[ProblemInfo]:
    rows = await conn.fetch(
        """
        WITH main_stats AS (
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
                AND e.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
            GROUP BY erh.problem_name
        ),
        error_stats AS (
            SELECT
                problem_name,
                json_agg(
                    jsonb_build_object('error_code', error_code::text, 'num_errors', num_errors)
                ) AS error_code_distribution,
                SUM(num_errors) AS num_total_runs
            FROM (
                SELECT
                    er.problem_name,
                    er.error_code,
                    COUNT(*) AS num_errors
                FROM evaluation_runs er
                    JOIN evaluations e ON er.evaluation_id = e.evaluation_id
                    JOIN agents a on e.agent_id = a.agent_id
                WHERE er.error_code IS NOT NULL
                    AND e.set_id = (SELECT MAX(set_id) FROM evaluation_sets)
                    AND a.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
                    AND e.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
                GROUP BY er.problem_name, er.error_code
            )
            GROUP BY problem_name
        ),
        token_stats AS (
            SELECT
                problem_name,
                json_agg(
                    jsonb_build_object('model', model, 'num_tokens', num_tokens)
                ) AS token_distribution
            FROM (
                SELECT
                    er.problem_name,
                    i.model,
                    SUM(i.num_input_tokens) AS num_tokens
                FROM evaluation_runs er
                    JOIN inferences i ON er.evaluation_run_id = i.evaluation_run_id 
                    JOIN evaluations e ON er.evaluation_id = e.evaluation_id 
                    JOIN agents a ON e.agent_id = a.agent_id 
                WHERE e.set_id = (SELECT MAX(set_id) FROM evaluation_sets)
                    AND a.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
                    AND e.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
                GROUP BY er.problem_name, i.model
            )
            GROUP BY problem_name
        ),
        top_agents_stats AS (
            SELECT
                problem_name,
                json_agg(
                    jsonb_build_object(
                        'agent_id', agent_id, 
                        'name', name,
                        'version', version_num,
                        'run_time', run_time
                    ) ORDER BY run_time ASC
                ) AS top_agents_run_on_problem
            FROM (
                SELECT
                    erh.problem_name,
                    a.agent_id,
                    a.name,
                    a.version_num,
                    EXTRACT(EPOCH FROM (erh.finished_or_errored_at - erh.created_at)) AS run_time,
                    ROW_NUMBER() OVER (PARTITION BY erh.problem_name ORDER BY EXTRACT(EPOCH FROM (erh.finished_or_errored_at - erh.created_at)) ASC) AS time_rank
                FROM evaluation_runs_hydrated erh
                    JOIN evaluations e ON erh.evaluation_id = e.evaluation_id
                    JOIN agents a ON e.agent_id = a.agent_id
                WHERE erh.status = 'finished'
                    AND erh.finished_or_errored_at IS NOT NULL
                    AND erh.created_at IS NOT NULL
                    AND e.set_id = (SELECT MAX(set_id) FROM evaluation_sets)
                    AND a.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
                    AND e.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
            ) ranked_agents
            WHERE time_rank <= 5
            GROUP BY problem_name
        )
        SELECT 
            ms.*,
            esa.error_code_distribution,
            ts.token_distribution,
            ta.top_agents_run_on_problem
        FROM main_stats ms 
        LEFT JOIN error_stats esa ON ms.problem_name = esa.problem_name
        LEFT JOIN token_stats ts ON ms.problem_name = ts.problem_name
        LEFT JOIN top_agents_stats ta ON ms.problem_name = ta.problem_name;
        """
    )

    problem_stats = [ProblemInfo(**row) for row in rows]

    evaluation_set_problems = await get_all_evaluation_set_problems_in_latest_set_id()
    for evaluation_set_problem in evaluation_set_problems:
        if not any(problem_stat.problem_name == evaluation_set_problem.problem_name for problem_stat in problem_stats):
            problem_stats.append(ProblemInfo(
                problem_name=evaluation_set_problem.problem_name,
                total_num_evaluation_runs=0,
                num_finished_evaluation_runs=0,
                num_finished_passed_evaluation_runs=0,
                num_finished_failed_evaluation_runs=0,
                num_errored_evaluation_runs=0,
                error_code_distribution=[],
                token_distribution=[],
                top_agents_run_on_problem=[],
                pass_rate=None,
                average_time=None,
                average_cost_usd=None,
                in_screener_1_set_group=any(_evaluation_set_problem.problem_name == evaluation_set_problem.problem_name and _evaluation_set_problem.set_group == EvaluationSetGroup.screener_1 for _evaluation_set_problem in evaluation_set_problems),
                in_screener_2_set_group=any(_evaluation_set_problem.problem_name == evaluation_set_problem.problem_name and _evaluation_set_problem.set_group == EvaluationSetGroup.screener_2 for _evaluation_set_problem in evaluation_set_problems),
                in_validator_set_group=any(_evaluation_set_problem.problem_name == evaluation_set_problem.problem_name and _evaluation_set_problem.set_group == EvaluationSetGroup.validator for _evaluation_set_problem in evaluation_set_problems)
            ))

    return problem_stats
