from dotenv import load_dotenv
from fastapi import APIRouter, Depends

from api.src.utils.auth import verify_request_public
import utils.logger as logger
from api.src.backend.entities import QuestionSolveRateStats
from api.src.backend.entities import MinerAgentWithScores
from models.evaluation_run import EvaluationRunStatus
from models.evaluation_set import EvaluationSetGroup

load_dotenv()

router = APIRouter()

@router.get("/solved-percentage-per-question", tags=["benchmarks"], dependencies=[Depends(verify_request_public)])
async def get_solved_percentage_per_question():
    """
    Returns the percentage of runs where each question was solved, as well as the number of runs and other relevant stats
    """
    async with get_db_connection() as conn:
        solved_results = await conn.fetch(f"""
            SELECT
                problem_name,
                    ROUND(
                    (COUNT(CASE WHEN solved = true THEN 1 END) * 100.0 / COUNT(*)), 2
                ) as solved_percentage,
                COUNT(*) as total_runs,
                COUNT(CASE WHEN solved THEN 1 END) as solved_runs,
                COUNT(CASE WHEN NOT solved THEN 1 END) as not_solved_runs
            FROM evaluation_runs
            WHERE solved IS NOT NULL
                AND status != '{EvaluationRunStatus.error}'  -- exclude errored runs
                AND problem_name in (select es.problem_name from evaluation_sets es where set_id = 3 and set_group='{EvaluationSetGroup.validator.value}')
            GROUP BY problem_name
            ORDER BY solved_percentage DESC, total_runs DESC;
        """)

        return [QuestionSolveRateStats(**dict(row)) for row in solved_results]

@router.get("/solving-agents", tags=["benchmarks"], dependencies=[Depends(verify_request_public)])
async def get_top_agents_solved_for_question(problem_name: str) -> list[MinerAgentWithScores]:
    async with get_db_connection() as conn:
        solving_agents = await conn.fetch("""
            SELECT a.agent_id, a.miner_hotkey, a.name, a.version_num, a.created_at, a.status, e.set_id, ass.final_score as score
                FROM evaluation_runs_hydrated r
            LEFT JOIN evaluations e ON e.evaluation_id = r.evaluation_id
            RIGHT JOIN agents a ON a.agent_id = e.agent_id
            LEFT JOIN agent_scores ass ON a.agent_id = ass.agent_id
                WHERE r.problem_name = $1
                AND solved = true
            ORDER BY ass.final_score DESC
            LIMIT 5;                         
        """, problem_name)


        return [MinerAgentWithScores(**dict(row)) for row in solving_agents]
