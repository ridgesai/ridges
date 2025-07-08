from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
import logging
from typing import List
import requests
import os
from dotenv import load_dotenv

from api.src.utils.auth import verify_request
from api.src.db.operations import DatabaseManager
from api.src.utils.models import AgentSummary, AgentQueryResponse, AgentVersionDetails, AgentSummaryResponse, RunningAgentEval, DashboardStats, EvaluationRunResponse
from api.src.db.s3 import S3Manager
from api.src.socket.websocket_manager import WebSocketManager
from api.src.utils.subtensor import get_daily_earnings_by_hotkey

load_dotenv()

logger = logging.getLogger(__name__)

db = DatabaseManager()
s3_manager = S3Manager()

async def get_agent_version_file(version_id: str):
    agent_version = await db.get_agent_version(version_id)
    
    if not agent_version:
        logger.info(f"File for agent version {version_id} was requested but not found in our database")
        raise HTTPException(
            status_code=404, 
            detail="The requested agent version was not found. Are you sure you have the correct version ID?"
        )
    
    try:
        agent_object = await s3_manager.get_file_object(f"{version_id}/agent.py")
    except Exception as e:
        logger.error(f"Error retrieving agent version file from S3 for version {version_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving agent version file. Please try again later."
        )
    
    async def file_generator():
        agent_object.seek(0)
        while True:
            chunk = agent_object.read(8192)  # Read in 8KB chunks
            if not chunk:
                break
            yield chunk
    
    headers = {
        "Content-Disposition": f'attachment; filename="agent.py"'
    }
    return StreamingResponse(file_generator(), media_type='application/octet-stream', headers=headers)

async def get_top_agents(num_agents: int = 3, include_code: bool = False) -> List[AgentSummary]:
    agent_summaries = await db.get_top_agents(num_agents)

    if not agent_summaries:
        logger.warning(f"Top agents endpoint was requested but no agents were found in the database")
        raise HTTPException(
            status_code=404,
            detail="No agents found"
        )

    if include_code:
        for agent_summary in agent_summaries:
            agent_summary.code = await s3_manager.get_file_text(f"{agent_summary.latest_version.version_id}/agent.py")

    return agent_summaries

async def get_agent(agent_id: str= None, miner_hotkey: str= None, include_code: bool = False) -> AgentQueryResponse:
    if not agent_id and not miner_hotkey:
        raise HTTPException(
            status_code=400,
            detail="Either agent_id or miner_hotkey must be provided"
        )
    
    if agent_id:
        latest_agent = await db.get_latest_agent(agent_id, scored=False)
        latest_scored_agent = await db.get_latest_agent(agent_id, scored=True)
    else:
        latest_agent = await db.get_latest_agent_by_miner_hotkey(miner_hotkey, scored=False)
        latest_scored_agent = await db.get_latest_agent_by_miner_hotkey(miner_hotkey, scored=True)

    if not latest_agent and not latest_scored_agent:
        logger.info(f"Agent {agent_id} was requested but not found in our database")
        raise HTTPException(
            status_code=404,
            detail="Agent not found"
        )
    
    if include_code:
        latest_agent.code = await s3_manager.get_file_text(f"{latest_agent.latest_version.version_id}/agent.py")
        if latest_scored_agent:
            latest_scored_agent.code = await s3_manager.get_file_text(f"{latest_scored_agent.latest_version.version_id}/agent.py")

    if latest_scored_agent and latest_scored_agent.latest_version.version_id == latest_agent.latest_version.version_id:
        latest_scored_agent = None

    if miner_hotkey:
        agent_id = (await db.get_agent_by_hotkey(miner_hotkey)).agent_id

    return AgentQueryResponse(
        agent_id=str(agent_id),
        latest_agent=latest_agent,
        latest_scored_agent=latest_scored_agent
    )

async def get_agent_version_code(version_id: str):
    agent_version = await db.get_agent_version(version_id)
    if not agent_version:
        logger.info(f"Agent version {version_id} was requested but not found in our database")
        raise HTTPException(
            status_code=404,
            detail="Agent version not found"
        )
    
    try:
        text = await s3_manager.get_file_text(f"{version_id}/agent.py")
    except Exception as e:
        logger.error(f"Error retrieving agent version code from S3 for version {version_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving agent version code. Please try again later."
        )
    
    return text

async def get_running_evaluations() -> list[RunningAgentEval]:
    try: 
        current_evaluations = await db.get_current_evaluations()
        return current_evaluations
    except Exception as e:
        logger.error(f"Error retrieving currently running evals: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving currently running evaluations. Please try again later."
        )

def get_total_rewards_per_day():

    url = "https://api.taostats.io/api/dtao/pool/latest/v1?page=1"

    headers = {"accept": "application/json",  "Authorization": os.getenv('TAOSTATS_API_KEY')}

    try:
        response = requests.get(url, headers=headers, params={"netuid": "62"})
        alpha_in_pool = int(response.json()['data'][0]['alpha_in_pool'])
        tao_in_pool = int(response.json()['data'][0]['total_tao'])
    except Exception as e:
        logger.error(f"Error retrieving tao and alpha in pool from Taostats: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving tao and alpha in pool. Please try again later."
        )

    url = "https://api.taostats.io/api/price/latest/v1?asset=tao"

    headers = {"accept": "application/json",  "Authorization": os.getenv('TAOSTATS_API_KEY')}

    response = requests.get(url, headers=headers)

    try:
        tao_price = float(response.json()['data'][0]['price'])
    except Exception as e:
        logger.error(f"Error retrieving tao price from Taostats: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving tao price. Please try again later."
        )
    
    tao_per_alpha = tao_in_pool / alpha_in_pool
    usd_per_day = tao_per_alpha * tao_price * 7200 * 0.41

    return usd_per_day

async def get_connected_validators():
    try:
        validators = await WebSocketManager.get_instance().get_connected_validators()
    except Exception as e:
        logger.error(f"Error retrieving connected validators: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving connected validators. Please try again later."
        )
    
    return validators

async def get_agent_summary(agent_id: str = None, miner_hotkey: str = None, include_code: bool = False) -> AgentSummaryResponse:
    if not agent_id and not miner_hotkey:
        raise HTTPException(
            status_code=400,
            detail="Either agent_id or miner_hotkey must be provided"
        )
    
    agent_summary = await db.get_agent_summary(agent_id, miner_hotkey)

    if not agent_summary:
        raise HTTPException(
            status_code=404,
            detail="Agent not found"
        )
    
    agent_summary.daily_earnings = 0 # await get_daily_earnings_by_hotkey(agent_summary.agent_details.miner_hotkey)

    if include_code:
        agent_summary.latest_version.code = await s3_manager.get_file_text(f"{agent_summary.latest_version.version_id}/agent.py")
    
    return agent_summary

async def get_queue_info(version_id: str):
    try:
        queue_info = await db.get_queue_info(version_id)
    except Exception as e:
        logger.error(f"Error retrieving queue info for version {version_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving queue info. Please try again later."
        )
    
    return queue_info

async def get_evaluations(version_id: str):
    try:
        evaluations = await db.get_evaluations(version_id)
    except Exception as e:
        logger.error(f"Error retrieving evaluations for version {version_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving evaluations. Please try again later."
        )
    
    return evaluations

async def get_runs_for_evaluation(evaluation_id: str) -> list[EvaluationRunResponse]:
    try:
        runs = await db.get_runs_for_evaluation(evaluation_id)
    except Exception as e:
        logger.error(f"Error retrieving runs for evaluation {evaluation_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving runs for evaluation. Please try again later."
        )
    
    return runs

async def get_statistics() -> DashboardStats:
    """
    Retrieves stats on the health of the network, primarily for the dashboard
    """
    try:
        statistics = await db.get_dashboard_statistics()
    except Exception as e:
        logger.error(f"Error retrieving dashboard statistics: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving dashboard stats. Please try again later."
        )
    
    return statistics

async def get_pool_status():
    """Get connection pool status for debugging."""
    try:
        pool_status = await db.get_pool_status()
        return pool_status
    except Exception as e:
        logger.error(f"Error retrieving pool status: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving pool status. Please try again later."
        )

router = APIRouter()

routes = [
    ("/agent-version-file", get_agent_version_file), 
    ("/top-agents", get_top_agents), 
    ("/agent", get_agent), 
    ("/agent-version-code", get_agent_version_code), 
    # ("/total-rewards-per-day", get_total_rewards_per_day), 
    ("/connected-validators", get_connected_validators), 
    ("/agent-summary", get_agent_summary), 
    ("/get-running-evaluations", get_running_evaluations),
    ("/queue-info", get_queue_info), 
    ("/evaluations", get_evaluations),
    ("/runs-for-evaluation", get_runs_for_evaluation), 
    ("/subnet-stats", get_statistics),
    ("/pool-status", get_pool_status) 
]

for path, endpoint in routes:
    router.add_api_route(
        path,
        endpoint,
        tags=["retrieval"],
        dependencies=[Depends(verify_request)],
        methods=["GET"]
    )
