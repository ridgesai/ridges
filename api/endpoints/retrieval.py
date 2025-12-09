from uuid import UUID
import asyncio
from typing import List
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from models.evaluation_set import EvaluationSetGroup
from models.evaluation import Evaluation, EvaluationWithRuns
from models.evaluation_set import EvaluationSetGroup
from models.agent import Agent, AgentScored, AgentStatus
from models.agent import Agent, AgentScored

from queries.agent import get_agents_in_queue, get_all_agents_by_hotkey, get_top_agents, get_agent_by_id, get_latest_agent_for_hotkey
from queries.statistics import TopScoreOverTime, agents_created_24_hrs, get_top_scores_over_time, score_improvement_24_hrs, top_score
from queries.evaluation import get_evaluations_for_agent_id
from queries.evaluation_run import get_all_evaluation_runs_in_evaluation_id

from utils.ttl import ttl_cache
from utils.s3 import download_text_file_from_s3
import utils.logger as logger

from api.endpoints.validator import get_all_connected_validator_ip_addresses



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

# /retrieval/agent-by-id?agent_id=
@router.get("/agent-by-id")
async def agent_by_id(agent_id: UUID) -> Agent:
    agent = await get_agent_by_id(agent_id)
    
    if agent is None:
        raise HTTPException(
            status_code=404,
            detail=f"Agent with ID {agent_id} not found"
        )

    return agent

# /retrieval/agent-by-hotkey?miner_hotkey=
@router.get("/agent-by-hotkey")
async def agent_by_hotkey(miner_hotkey: str) -> Agent:
    agent = await get_latest_agent_for_hotkey(miner_hotkey=miner_hotkey)
    
    if agent is None:
        raise HTTPException(
            status_code=404,
            detail=f"Agent with miner hotkey {miner_hotkey} not found"
        )

    return agent

# /retrieval/all-agents-by-hotkey?miner_hotkey=
@router.get("/all-agents-by-hotkey")
async def all_agents_by_hotkey(miner_hotkey: str) -> list[Agent]:
    agents = await get_all_agents_by_hotkey(miner_hotkey=miner_hotkey)
    return agents

# TODO ADAM: optimize that
# /retrieval/evaluations-for-agent?agent_id=
@router.get("/evaluations-for-agent")
async def evaluations_for_agent(agent_id: str) -> list[EvaluationWithRuns]:
    evaluations: list[Evaluation] = await get_evaluations_for_agent_id(agent_id=UUID(agent_id))
    
    runs_per_eval = await asyncio.gather(
        *[get_all_evaluation_runs_in_evaluation_id(evaluation_id=e.evaluation_id) for e in evaluations]
    )

    return [
        EvaluationWithRuns(**e.model_dump(), runs=runs)
        for e, runs in zip(evaluations, runs_per_eval)
    ]

# /retrieval/agent-code?agent_id=
@router.get("/agent-code")
async def get_agent_code(agent_id: str, request: Request) -> str:
    agent_version = await get_agent_by_id(agent_id=agent_id)
    
    if not agent_version:
        logger.info(f"File for agent version {agent_id} was requested but not found in our database")
        raise HTTPException(
            status_code=404, 
            detail="The requested agent version was not found. Are you sure you have the correct version ID?"
        )
    
    if agent_version.status in [AgentStatus.screening_1, AgentStatus.screening_2, AgentStatus.evaluating]:
        client_ip = request.client.host
        
        connected_validator_ips = get_all_connected_validator_ip_addresses()

        if client_ip not in connected_validator_ips:
            logger.warning(f"Unauthorized IP {client_ip} attempted to access agent code for version {agent_id}")
            raise HTTPException(
                status_code=403,
                detail="Access denied: IP not authorized"
            )
    
    try:
        text = await download_text_file_from_s3(f"{agent_id}/agent.py")
    except Exception as e:
        logger.error(f"Error retrieving agent version code from S3 for version {agent_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving agent version code. Please try again later."
        )
    
    return text

# /retrieval/top-scores-over-time
@router.get("/top-scores-over-time")
@ttl_cache(ttl_seconds=60 * 15) # 15 minutes
async def top_scores_over_time() -> List[TopScoreOverTime]:
    return await get_top_scores_over_time()

class NetworkStatisticsResponse(BaseModel):
    score_improvement_24_hrs: float
    agents_created_24_hrs: int
    top_score: float

# /retrieval/network-statistics
@router.get("/network-statistics")
@ttl_cache(ttl_seconds=60 * 15) # 15 minutes
async def network_statistics() -> NetworkStatisticsResponse:
    return NetworkStatisticsResponse(
        score_improvement_24_hrs=await score_improvement_24_hrs(),
        agents_created_24_hrs=await agents_created_24_hrs(),
        top_score=await top_score()
    )
