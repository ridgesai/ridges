from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
import logging
from dotenv import load_dotenv

from api.src.utils.auth import verify_request
from api.src.utils.s3 import S3Manager
from api.src.socket.websocket_manager import WebSocketManager
from api.src.backend.queries.agents import get_latest_agent as db_get_latest_agent, get_agent_by_version_id
from api.src.backend.entities import EvaluationRun, MinerAgent
from api.src.backend.queries.evaluations import get_evaluations_for_agent_version, get_runs_for_evaluation as db_get_runs_for_evaluation, get_queue_info as db_get_queue_info
from api.src.backend.queries.statistics import get_24_hour_statistics, get_currently_running_evaluations, RunningEvaluation, get_top_agents as db_get_top_agents, get_agent_summary_by_hotkey

load_dotenv()

logger = logging.getLogger(__name__)

s3_manager = S3Manager()

async def get_agent_code(version_id: str, return_as_text: bool = False):
    agent_version = await get_agent_by_version_id(version_id=version_id)
    
    if not agent_version:
        logger.info(f"File for agent version {version_id} was requested but not found in our database")
        raise HTTPException(
            status_code=404, 
            detail="The requested agent version was not found. Are you sure you have the correct version ID?"
        )
    
    if return_as_text:
        try:
            text = await s3_manager.get_file_text(f"{version_id}/agent.py")
        except Exception as e:
            logger.error(f"Error retrieving agent version code from S3 for version {version_id}: {e}")
            raise HTTPException(
                status_code=500,
                detail="Internal server error while retrieving agent version code. Please try again later."
            )
        
        return text

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

async def get_queue_info(version_id: str):
    try:
        queue_info = await db_get_queue_info(version_id)
    except Exception as e:
        logger.error(f"Error retrieving queue info for version {version_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving queue info. Please try again later."
        )
    
    return queue_info

async def get_evaluations(version_id: str):
    try:
        evaluations = await get_evaluations_for_agent_version(version_id)
    except Exception as e:
        logger.error(f"Error retrieving evaluations for version {version_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving evaluations. Please try again later."
        )
    
    return evaluations

async def get_runs_for_evaluation(evaluation_id: str) -> list[EvaluationRun]:
    try:
        runs = await db_get_runs_for_evaluation(evaluation_id)
    except Exception as e:
        logger.error(f"Error retrieving runs for evaluation {evaluation_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving runs for evaluation. Please try again later."
        )
    
    return runs

async def get_latest_agent(miner_hotkey: str = None):
    if not miner_hotkey:
        raise HTTPException(
            status_code=400,
            detail="miner_hotkey must be provided"
        )
    
    latest_agent = await db_get_latest_agent(miner_hotkey=miner_hotkey)

    if not latest_agent:
        logger.info(f"Agent {miner_hotkey} was requested but not found in our database")
        raise HTTPException(
            status_code=404,
            detail="Agent not found"
        )
    
    return latest_agent

async def get_network_stats():
    """
    Gets statistics on the number of agents, score changes, etc. Primarily ingested by the dashboard
    """
    statistics_24_hrs = await get_24_hour_statistics()

    return statistics_24_hrs

async def get_running_evaluations() -> list[RunningEvaluation]:
    """
    Gets a list of currently running evaluations to display on dashboard
    """
    evaluations = await get_currently_running_evaluations()

    return evaluations

async def get_top_agents(num_agents: int = 3) -> list[MinerAgent]:
    """
    Gets a list of current high score agents
    """
    if num_agents < 1:
        raise HTTPException(
            status_code=500,
            detail="Must provide a fixed number of agents"
        )
    
    top_agents = await db_get_top_agents(num_agents=num_agents)

    return top_agents

async def agent_summary_by_hotkey(miner_hotkey: str) -> list[MinerAgent]:
    """
    Returns a list of every version of an agent submitted by a hotkey including its score. Used by the dashboard to render stats about the miner
    """
    agent_versions = await get_agent_summary_by_hotkey(miner_hotkey=miner_hotkey)
    
    if agent_versions is None: 
        raise HTTPException(
            status_code=500,
            detail="Error loading details for agent"
        )

    return agent_versions


router = APIRouter()

routes = [
    ("/agent-version-file", get_agent_code), 
    ("/connected-validators", get_connected_validators), 
    ("/queue-info", get_queue_info), 
    ("/evaluations", get_evaluations),
    ("/runs-for-evaluation", get_runs_for_evaluation), 
    ("/latest-agent", get_latest_agent),
    ("/network-stats", get_network_stats),
    ("/running-evaluations", get_running_evaluations),
    ("/top-agents", get_top_agents),
    ("/agent-by-hotkey", agent_summary_by_hotkey),
]

for path, endpoint in routes:
    router.add_api_route(
        path,
        endpoint,
        tags=["retrieval"],
        dependencies=[Depends(verify_request)],
        methods=["GET"]
    )
