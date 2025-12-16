import asyncio

from uuid import UUID
from pydantic import BaseModel
from utils.ttl import ttl_cache
from typing import List, Optional
from fastapi import APIRouter, HTTPException
from utils.s3 import download_text_file_from_s3
from models.evaluation_set import EvaluationSetGroup
from queries.evaluation import get_evaluations_for_agent_id
from models.evaluation import Evaluation, EvaluationWithRuns
from queries.evaluation_run import get_all_evaluation_runs_in_evaluation_id
from models.agent import Agent, AgentScored, AgentStatus, BenchmarkAgentScored, PossiblyBenchmarkAgent
from queries.statistics import NumPerfectlySolvedForTimeBucket, TopScoreOverTime, get_top_scores_over_time, top_score, agents_created_24_hrs, get_perfectly_solved_over_time, score_improvement_24_hrs
from queries.agent import get_top_agents, get_agent_by_id, get_agents_in_queue, get_benchmark_agents, get_all_agents_by_miner_hotkey, get_latest_agent_for_miner_hotkey, get_possibly_benchmark_agent_by_id



router = APIRouter()



# /retrieval/queue?stage={screener_1|screener_2|validator}
@router.get("/queue")
@ttl_cache(ttl_seconds=60) # 1 minute
async def queue(stage: EvaluationSetGroup) -> List[Agent]:
    return await get_agents_in_queue(stage)



# /retrieval/top-agents
@router.get("/top-agents")
@ttl_cache(ttl_seconds=60) # 1 minute
async def top_agents() -> List[AgentScored]:
    return await get_top_agents(number_of_agents=50)



# /retrieval/benchmark-agents
@router.get("/benchmark-agents")
@ttl_cache(ttl_seconds=10*60) # 10 minutes
async def benchmark_agents() -> List[BenchmarkAgentScored]:
    return await get_benchmark_agents()



# /retrieval/agent-by-id?agent_id=
@router.get("/agent-by-id")
async def agent_by_id(agent_id: UUID) -> PossiblyBenchmarkAgent:
    agent = await get_possibly_benchmark_agent_by_id(agent_id)
    
    if agent is None:
        raise HTTPException(
            status_code=404,
            detail=f"Agent with ID {agent_id} not found"
        )

    return agent

# /retrieval/agent-by-hotkey?miner_hotkey=
@router.get("/agent-by-hotkey")
async def agent_by_hotkey(miner_hotkey: str) -> Agent:
    agent = await get_latest_agent_for_miner_hotkey(miner_hotkey=miner_hotkey)
    
    if agent is None:
        raise HTTPException(
            status_code=404,
            detail=f"Agent with miner hotkey {miner_hotkey} not found"
        )

    return agent

# /retrieval/all-agents-by-hotkey?miner_hotkey=
@router.get("/all-agents-by-hotkey")
async def all_agents_by_hotkey(miner_hotkey: str) -> List[Agent]:
    agents = await get_all_agents_by_miner_hotkey(miner_hotkey=miner_hotkey)
    return agents



# TODO ADAM: optimize
# /retrieval/evaluations-for-agent?agent_id=
@router.get("/evaluations-for-agent")
async def evaluations_for_agent(agent_id: UUID) -> List[EvaluationWithRuns]:
    evaluations: List[Evaluation] = await get_evaluations_for_agent_id(agent_id=agent_id)
    
    runs_per_eval = await asyncio.gather(
        *[get_all_evaluation_runs_in_evaluation_id(evaluation_id=e.evaluation_id) for e in evaluations]
    )

    return [
        EvaluationWithRuns(**e.model_dump(), runs=runs)
        for e, runs in zip(evaluations, runs_per_eval)
    ]



# /retrieval/agent-code?agent_id=
@router.get("/agent-code")
async def agent_code(agent_id: UUID) -> str:
    agent = await get_agent_by_id(agent_id=agent_id)
    
    if not agent:
        raise HTTPException(
            status_code=404, 
            detail=f"Agent with ID {agent_id} not found"
        )
    
    if agent.status in [AgentStatus.screening_1, AgentStatus.screening_2, AgentStatus.evaluating]:
        raise HTTPException(
            status_code=403,
            detail=f"Agent {agent.agent_id} is still being screened/evaluated"
        )
    
    return await download_text_file_from_s3(f"{agent_id}/agent.py")



# /retrieval/top-scores-over-time
@router.get("/top-scores-over-time")
@ttl_cache(ttl_seconds=60 * 15) # 15 minutes
async def top_scores_over_time() -> List[TopScoreOverTime]:
    return await get_top_scores_over_time()


# /retrieval/perfectly-solved-over-time
@router.get("/perfectly-solved-over-time")
@ttl_cache(ttl_seconds=60 * 15) # 15 minutes
async def perfectly_solved_over_time() -> List[NumPerfectlySolvedForTimeBucket]:
    return await get_perfectly_solved_over_time()



# /retrieval/network-statistics
class NetworkStatisticsResponse(BaseModel):
    score_improvement_24_hrs: float
    agents_created_24_hrs: int
    top_score: Optional[float]

@router.get("/network-statistics")
@ttl_cache(ttl_seconds=60 * 15) # 15 minutes
async def network_statistics() -> NetworkStatisticsResponse:
    return NetworkStatisticsResponse(
        score_improvement_24_hrs=await score_improvement_24_hrs(),
        agents_created_24_hrs=await agents_created_24_hrs(),
        top_score=await top_score()
    )