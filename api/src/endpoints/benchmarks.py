from dotenv import load_dotenv
from fastapi import APIRouter, Depends

from api.src.utils.auth import verify_request_public
from loggers.logging_utils import get_logger
from api.src.backend.entities import QuestionSolveRateStats
from api.src.backend.db_manager import get_db_connection
from api.src.backend.entities import MinerAgentWithScores

load_dotenv()

logger = get_logger(__name__)

async def get_solved_percentage_per_question():
    """
    Returns the percentage of runs where each question was solved, as well as the number of runs and other relevant stats
    """
    async with get_db_connection() as conn:
        solved_results = await conn.fetch("""
            SELECT
                swebench_instance_id,
                    ROUND(
                    (COUNT(CASE WHEN solved = true THEN 1 END) * 100.0 / COUNT(*)), 2
                ) as solved_percentage,
                COUNT(*) as total_runs,
                COUNT(CASE WHEN solved THEN 1 END) as solved_runs,
                COUNT(CASE WHEN NOT solved THEN 1 END) as not_solved_runs
            FROM evaluation_runs
            WHERE solved IS NOT NULL
                AND status != 'cancelled'  -- exclude cancelled runs
                AND swebench_instance_id in (select es.swebench_instance_id from evaluation_sets es where set_id = 3 and type='validator')
            GROUP BY swebench_instance_id
            ORDER BY solved_percentage DESC, total_runs DESC;
        """)

        return [QuestionSolveRateStats(**dict(row)) for row in solved_results]

async def get_top_agents_solved_for_question(swebench_instance_id: str) -> list[MinerAgentWithScores]:
    async with get_db_connection() as conn:
        solving_agents = await conn.fetch("""
            SELECT a.version_id, a.miner_hotkey, a.agent_name, a.version_num, a.created_at, a.status, e.set_id, ass.final_score as score
                FROM evaluation_runs r
            LEFT JOIN evaluations e ON e.evaluation_id = r.evaluation_id
            RIGHT JOIN miner_agents a ON a.version_id = e.version_id
            LEFT JOIN agent_scores ass ON a.version_id = ass.version_id
                WHERE r.swebench_instance_id = $1
                AND solved = true
            ORDER BY ass.final_score DESC
            LIMIT 5;                         
        """, swebench_instance_id)


        return [MinerAgentWithScores(**dict(row)) for row in solving_agents]

router = APIRouter()

routes = [
    ("/solved-percentage-per-question", get_solved_percentage_per_question, ["GET"]),
    ("/solving-agents", get_top_agents_solved_for_question, ["GET"]),
]

for path, endpoint, methods in routes:
    router.add_api_route(
        path,
        endpoint,
        tags=["benchmarks"],
        dependencies=[Depends(verify_request_public)],
        methods=methods
    )
