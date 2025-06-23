from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
import logging
from typing import List
import requests
import os
from dotenv import load_dotenv

from api.src.utils.auth import verify_request
from api.src.db.operations import DatabaseManager
from api.src.utils.models import AgentSummary, AgentQueryResponse
from api.src.db.s3 import S3Manager

load_dotenv()

logger = logging.getLogger(__name__)

db = DatabaseManager()
s3_manager = S3Manager()

def get_agent_version_file(version_id: str):
    agent_version = db.get_agent_version(version_id)
    
    if not agent_version:
        logger.info(f"File for agent version {version_id} was requested but not found in our database")
        raise HTTPException(
            status_code=404, 
            detail="The requested agent version was not found. Are you sure you have the correct version ID?"
        )
    
    try:
        agent_object = s3_manager.get_file_object(f"{version_id}/agent.py")
    except Exception as e:
        logger.error(f"Error retrieving agent version file from S3 for version {version_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving agent version file. Please try again later."
        )
    
    headers = {
        "Content-Disposition": f'attachment; filename="agent.py"'
    }
    return StreamingResponse(agent_object, media_type='application/octet-stream', headers=headers)

def get_top_agents(num_agents: int = 3, include_code: bool = False) -> List[AgentSummary]:
    agent_summaries = db.get_top_agents(num_agents)

    if not agent_summaries:
        logger.warning(f"Top agents endpoint was requested but no agents were found in the database")
        raise HTTPException(
            status_code=404,
            detail="No agents found"
        )

    if include_code:
        for agent_summary in agent_summaries:
            agent_summary.code = s3_manager.get_file_text(f"{agent_summary.latest_version.version_id}/agent.py")

    return agent_summaries

def get_agent(agent_id: str, include_code: bool = False) -> AgentQueryResponse:
    latest_agent = db.get_latest_agent(agent_id, scored=False)
    latest_scored_agent = db.get_latest_agent(agent_id, scored=True)

    if not latest_agent and not latest_scored_agent:
        logger.info(f"Agent {agent_id} was requested but not found in our database")
        raise HTTPException(
            status_code=404,
            detail="Agent not found"
        )
    
    if include_code:
        latest_agent.code = s3_manager.get_file_text(f"{latest_agent.latest_version.version_id}/agent.py")
        if latest_scored_agent:
            latest_scored_agent.code = s3_manager.get_file_text(f"{latest_scored_agent.latest_version.version_id}/agent.py")

    if latest_scored_agent and latest_scored_agent.latest_version.version_id == latest_agent.latest_version.version_id:
        latest_scored_agent = None

    return AgentQueryResponse(
        latest_agent=latest_agent,
        latest_scored_agent=latest_scored_agent
    )

def get_agent_version_code(version_id: str):
    agent_version = db.get_agent_version(version_id)
    if not agent_version:
        logger.info(f"Agent version {version_id} was requested but not found in our database")
        raise HTTPException(
            status_code=404,
            detail="Agent version not found"
        )
    
    try:
        text = s3_manager.get_file_text(f"{version_id}/agent.py")
    except Exception as e:
        logger.error(f"Error retrieving agent version code from S3 for version {version_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving agent version code. Please try again later."
        )
    
    return text

def get_recent_executions(num_executions: int = 3):
    executions = db.get_recent_executions(num_executions)

    if not executions:
        logger.warning(f"Recent executions endpoint was requested but no executions were found in the database")
        raise HTTPException(
            status_code=404,
            detail="No executions found"
        )

    return executions

def get_num_agents():
    return db.get_num_agents()

def get_total_rewards_24h():

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

router = APIRouter()

routes = [
    ("/agent-version-file", get_agent_version_file),
    ("/top-agents", get_top_agents),
    ("/agent", get_agent),
    ("/agent-version-code", get_agent_version_code),
    ("/recent-executions", get_recent_executions),
    ("/num-agents", get_num_agents),
    ("/total-rewards-24h", get_total_rewards_24h),
]

for path, endpoint in routes:
    router.add_api_route(
        path,
        endpoint,
        tags=["retrieval"],
        dependencies=[Depends(verify_request)],
        methods=["GET"]
    )
