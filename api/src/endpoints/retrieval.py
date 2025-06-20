from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
import logging
from typing import List

from api.src.utils.auth import verify_request
from api.src.db.operations import DatabaseManager
from api.src.utils.models import AgentSummary
from api.src.db.s3 import S3Manager

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

def get_agent(agent_id: str):
    latest_agent = db.get_latest_agent(agent_id, scored=False)
    latest_scored_agent = db.get_latest_agent(agent_id, scored=True)
    if not latest_agent and not latest_scored_agent:
        logger.info(f"Agent {agent_id} was requested but not found in our database")
        raise HTTPException(
            status_code=404,
            detail="Agent not found"
        )
    
    return {
        "latest_agent": latest_agent,
        "latest_scored_agent": latest_scored_agent
    }

router = APIRouter()

routes = [
    ("/agent-version-file", get_agent_version_file),
    ("/top-agents", get_top_agents),
    ("/agent", get_agent),
]

for path, endpoint in routes:
    router.add_api_route(
        path,
        endpoint,
        tags=["retrieval"],
        dependencies=[Depends(verify_request)],
        methods=["GET"]
    )
