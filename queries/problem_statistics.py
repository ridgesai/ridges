from pydantic import BaseModel
from typing import List, Optional
from models.problem import ProblemDifficulty
from models.evaluation_set import EvaluationSetGroup
from utils.database import db_operation, DatabaseConnection
from evaluator.problem_suites.problem_suite import ProblemSuiteName
from queries.evaluation_set import get_all_evaluation_set_problems_in_latest_set_id
from evaluator.problem_suites.polyglot.polyglot_suite import POLYGLOT_PY_SUITE, POLYGLOT_JS_SUITE
from evaluator.problem_suites.swebench_verified.swebench_verified_suite import SWEBENCH_VERIFIED_SUITE



class ProblemStatistics(BaseModel):
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

    problem_stats = [ProblemStatistics(**row) for row in rows]

    evaluation_set_problems = await get_all_evaluation_set_problems_in_latest_set_id()
    for evaluation_set_problem in evaluation_set_problems:
        if not any(problem_stat.problem_name == evaluation_set_problem.problem_name for problem_stat in problem_stats):
            problem_stats.append(ProblemStatistics(
                problem_name=evaluation_set_problem.problem_name,
                total_num_evaluation_runs=0,
                num_finished_evaluation_runs=0,
                num_finished_passed_evaluation_runs=0,
                num_finished_failed_evaluation_runs=0,
                num_errored_evaluation_runs=0,
                pass_rate=None,
                average_time=None,
                average_cost_usd=None,
                in_screener_1_set_group=any(_evaluation_set_problem.problem_name == evaluation_set_problem.problem_name and _evaluation_set_problem.set_group == EvaluationSetGroup.screener_1 for _evaluation_set_problem in evaluation_set_problems),
                in_screener_2_set_group=any(_evaluation_set_problem.problem_name == evaluation_set_problem.problem_name and _evaluation_set_problem.set_group == EvaluationSetGroup.screener_2 for _evaluation_set_problem in evaluation_set_problems),
                in_validator_set_group=any(_evaluation_set_problem.problem_name == evaluation_set_problem.problem_name and _evaluation_set_problem.set_group == EvaluationSetGroup.validator for _evaluation_set_problem in evaluation_set_problems)
            ))

    return problem_stats