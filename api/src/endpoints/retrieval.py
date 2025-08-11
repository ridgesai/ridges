from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional, Any
from fastapi.responses import StreamingResponse, PlainTextResponse
from api.src.models.screener import Screener
from loggers.logging_utils import get_logger
from dotenv import load_dotenv
from datetime import datetime

from api.src.utils.auth import verify_request
from api.src.utils.s3 import S3Manager
from api.src.socket.websocket_manager import WebSocketManager
from api.src.backend.entities import EvaluationRun, MinerAgent, EvaluationsWithHydratedRuns, Inference, EvaluationsWithHydratedUsageRuns, MinerAgentWithScores, ScreenerQueueByStage
from api.src.backend.queries.agents import get_latest_agent as db_get_latest_agent, get_agent_by_version_id, get_agents_by_hotkey
from api.src.backend.queries.evaluations import get_evaluation_by_evaluation_id, get_evaluations_for_agent_version, get_evaluations_with_usage_for_agent_version
from api.src.backend.queries.evaluations import get_queue_info as db_get_queue_info
from api.src.backend.queries.evaluation_runs import get_runs_for_evaluation as db_get_runs_for_evaluation, get_evaluation_run_logs as db_get_evaluation_run_logs
from api.src.backend.queries.statistics import get_24_hour_statistics, get_currently_running_evaluations, RunningEvaluation, get_agent_summary_by_hotkey
from api.src.backend.queries.statistics import get_top_agents as db_get_top_agents, get_queue_position_by_hotkey, QueuePositionPerValidator, get_inference_details_for_run
from api.src.backend.queries.statistics import get_agent_scores_over_time as db_get_agent_scores_over_time, get_miner_score_activity as db_get_miner_score_activity
from api.src.backend.queries.queue import get_queue_for_all_validators as db_get_queue_for_all_validators, get_screener_queue_by_stage as db_get_screener_queue_by_stage
from api.src.backend.queries.evaluation_sets import get_evaluation_set_instances, get_latest_set_id
from api.src.backend.entities import ProviderStatistics
from api.src.backend.queries.inference import get_inference_provider_statistics as db_get_inference_provider_statistics
from api.src.backend.internal_tools import InternalTools

load_dotenv()

logger = get_logger(__name__)

s3_manager = S3Manager()
internal_tools = InternalTools()

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
    """
    Returns a list of all connected validators and screener validators
    """
    try:
        validators = await WebSocketManager.get_instance().get_clients()
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

async def get_evaluations(version_id: str, set_id: Optional[int] = None) -> list[EvaluationsWithHydratedRuns]:
    try:
        # If no set_id provided, use the latest set_id
        if set_id is None:
            set_id = await get_latest_set_id()
        
        evaluations = await get_evaluations_for_agent_version(version_id, set_id)
    except Exception as e:
        logger.error(f"Error retrieving evaluations for version {version_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving evaluations. Please try again later."
        )
    
    return evaluations

async def get_evaluations_with_usage(version_id: str, set_id: Optional[int] = None, fast: bool = Query(default=True, description="Use fast single-query mode")) -> list[EvaluationsWithHydratedUsageRuns]:
    try:
        evaluations = await get_evaluations_with_usage_for_agent_version(version_id, set_id, fast=fast)
    except Exception as e:
        logger.error(f"Error retrieving evaluations for version {version_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving evaluations. Please try again later."
        )
    
    return evaluations

async def get_evaluation_run_logs(run_id: str) -> PlainTextResponse:
    try:
        logs = await db_get_evaluation_run_logs(run_id)
    except Exception as e:
        logger.error(f"Error retrieving logs for run {run_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving logs for run. Please try again later."
        )
    
    return PlainTextResponse(content=logs)

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

async def get_top_agents(num_agents: int = 3) -> list[MinerAgentWithScores]:
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

async def agent_scores_over_time(set_id: Optional[int] = None):
    """Gets agent scores over time for charting"""
    return await db_get_agent_scores_over_time(set_id)

async def miner_score_activity(set_id: Optional[int] = None):
    """Gets miner submissions and top scores by hour for correlation analysis"""
    return await db_get_miner_score_activity(set_id)

async def get_queue_position(miner_hotkey: str) -> list[QueuePositionPerValidator]:
    """
    Gives a list of where an agent is in queue for every validator
    """
    positions = await get_queue_position_by_hotkey(miner_hotkey=miner_hotkey)

    return positions

async def agent_summary_by_hotkey(miner_hotkey: str) -> list[MinerAgentWithScores]:
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

async def inferences_for_run(run_id: str) -> list[Inference]:
    """
    Returns a list of every version of an agent submitted by a hotkey including its score. Used by the dashboard to render stats about the miner
    """
    inferences = await get_inference_details_for_run(run_id=run_id)
    
    if inferences is None: 
        raise HTTPException(
            status_code=500,
            detail="Error loading inference calls for run"
        )

    return inferences

async def validator_queues():
    """
    Returns a list of every validator and their queue info
    """
    queue_info = await db_get_queue_for_all_validators()

    if queue_info is None:
        raise HTTPException(
            status_code=500,
            detail="Error loading queue info"
        )
    
    return queue_info

async def screener_queues() -> ScreenerQueueByStage:
    """Get screener queues by stage (stage 1 and stage 2)"""
    try:
        return await db_get_screener_queue_by_stage()
    except Exception as e:
        logger.error(f"Error getting screener queues: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving screener queues. Please try again later."
        )
    
async def get_agents_from_hotkey(miner_hotkey: str) -> list[MinerAgent]:
    """
    Returns a list of all agents for a given hotkey
    """
    try:
        agents = await get_agents_by_hotkey(miner_hotkey=miner_hotkey)
        return agents
    except Exception as e:
        logger.error(f"Error retrieving agents for hotkey {miner_hotkey}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving agents"
        )
    
async def get_inference_provider_statistics(start_time: datetime, end_time: datetime) -> list[ProviderStatistics]:
    """
    Returns statistics on inference provider performance
    """
    try:
        provider_statistics = await db_get_inference_provider_statistics(start_time=start_time, end_time=end_time)
        return provider_statistics
    except Exception as e:
        logger.error(f"Error retrieving inferences for last {start_time} to {end_time}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving inferences"
        )
    
async def get_emission_alpha_for_hotkey(miner_hotkey: str, hours: float) -> dict[str, Any]:
    """
    Returns the emission alpha for a given hotkey
    """
    try:
        amount = await internal_tools.get_emission_alpha_for_hotkey(miner_hotkey=miner_hotkey, hours=hours)
        return {"amount": amount, "hours": hours, "miner_hotkey": miner_hotkey}
    except Exception as e:
        logger.error(f"Error retrieving emission alpha for hotkey {miner_hotkey}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving emission alpha"
        )

router = APIRouter()

routes = [
    ("/agent-version-file", get_agent_code), 
    ("/connected-validators", get_connected_validators), 
    ("/queue-info", get_queue_info), 
    ("/evaluations", get_evaluations),
    ("/evaluations-with-usage", get_evaluations_with_usage),
    ("/evaluation-run-logs", get_evaluation_run_logs),
    ("/runs-for-evaluation", get_runs_for_evaluation), 
    ("/latest-agent", get_latest_agent),
    ("/network-stats", get_network_stats),
    ("/running-evaluations", get_running_evaluations),
    ("/top-agents", get_top_agents),
    ("/agent-by-hotkey", agent_summary_by_hotkey),
    ("/queue-position-by-hotkey", get_queue_position),
    ("/inferences-by-run", inferences_for_run),
    ("/validator-queues", validator_queues),
    ("/screener-queues", screener_queues),
    ("/agent-scores-over-time", agent_scores_over_time),
    ("/miner-score-activity", miner_score_activity),
    ("/agents-from-hotkey", get_agents_from_hotkey),    
    ("/inference-provider-statistics", get_inference_provider_statistics),
    ("/emission-alpha-for-hotkey", get_emission_alpha_for_hotkey)
]

for path, endpoint in routes:
    router.add_api_route(
        path,
        endpoint,
        tags=["retrieval"],
        dependencies=[Depends(verify_request)],
        methods=["GET"]
    )
