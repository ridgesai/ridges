from uuid import UUID
from typing import List
from models.agent import Agent, AgentScored
from fastapi import APIRouter, HTTPException
from models.evaluation_set import EvaluationSetGroup
from queries.agent import get_top_agents, get_agent_by_id, get_agents_in_queue, get_latest_agent_for_hotkey



router = APIRouter()



# /retrieval/queue?stage={screener_1|screener_2|validator}
@router.get("/queue")
async def queue(stage: EvaluationSetGroup) -> List[Agent]:
    return await get_agents_in_queue(stage)



# /retrieval/top-agents?number_of_agents={number_of_agents}&page={page}
@router.get("/top-agents")
async def top_agents(number_of_agents: int = 5, page: int = 1) -> List[AgentScored]:
    return await get_top_agents(number_of_agents=number_of_agents, page=page)



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