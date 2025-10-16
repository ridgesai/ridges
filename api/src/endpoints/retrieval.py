from fastapi import APIRouter, Depends, HTTPException, Query, Request
from typing import Optional, Any
from fastapi.responses import StreamingResponse, PlainTextResponse
from api.queries.statistics import score_improvement_24_hrs, agents_created_24_hrs, top_score
import utils.logger as logger
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone

from api.config import MINER_AGENT_UPLOAD_RATE_LIMIT_SECONDS
from api.src.utils.auth import verify_request_public
# from api.src.backend.entities import EvaluationRun, MinerAgent, EvaluationsWithHydratedRuns, Inference, EvaluationsWithHydratedUsageRuns, MinerAgentWithScores, ScreenerQueueByStage
# from api.src.backend.queries.agents import get_latest_agent as db_get_latest_agent, get_agent_by_agent_id, get_agents_by_hotkey
# from api.src.backend.queries.evaluations import get_evaluation_by_evaluation_id, get_evaluations_for_agent_version, get_evaluations_with_usage_for_agent_version
# from api.src.backend.queries.evaluations import get_queue_info as db_get_queue_info
# from api.src.backend.queries.evaluation_runs import get_runs_for_evaluation as db_get_runs_for_evaluation, get_evaluation_run_logs as db_get_evaluation_run_logs
# from api.src.backend.queries.statistics import get_24_hour_statistics, get_currently_running_evaluations, RunningEvaluation, get_agent_summary_by_hotkey
# from api.src.backend.queries.statistics import get_top_agents as db_get_top_agents, get_queue_position_by_hotkey, QueuePositionPerValidator, get_inference_details_for_run
from api.src.backend.queries.statistics import get_agent_scores_over_time as db_get_agent_scores_over_time 
# from api.src.backend.queries.statistics import get_miner_score_activity as db_get_miner_score_activity
# from api.queries.evaluation_set import get_latest_set_id
# from api.src.backend.entities import ProviderStatistics
# from api.src.backend.queries.inference import get_inference_provider_statistics as db_get_inference_provider_statistics
# from api.src.backend.queries.open_users import get_emission_dispersed_to_open_user as db_get_emission_dispersed_to_open_user, get_all_transactions as db_get_all_transactions, get_all_treasury_hotkeys as db_get_all_treasury_hotkeys
# from api.src.backend.queries.agents import get_all_approved_agent_ids as db_get_all_approved_agent_ids
# from api.src.backend.queries.open_users import get_total_dispersed_by_treasury_hotkeys as db_get_total_dispersed_by_treasury_hotkeys
from utils.s3 import download_text_file_from_s3


load_dotenv()

router = APIRouter()


# @router.get("/evaluations", tags=["retrieval"], dependencies=[Depends(verify_request_public)])
# async def get_evaluations(agent_id: str, set_id: Optional[int] = None) -> list[EvaluationsWithHydratedRuns]:
#     try:
#         # If no set_id provided, use the latest set_id
#         if set_id is None:
#             set_id = await get_latest_set_id()
        
#         evaluations = await get_evaluations_for_agent_version(agent_id, set_id)
#     except Exception as e:
#         logger.error(f"Error retrieving evaluations for version {agent_id}: {e}")
#         raise HTTPException(
#             status_code=500,
#             detail="Internal server error while retrieving evaluations. Please try again later."
#         )
    
#     return evaluations

# @router.get("/evaluations-with-usage", tags=["retrieval"], dependencies=[Depends(verify_request_public)])
# async def get_evaluations_with_usage(agent_id: str, set_id: Optional[int] = None, fast: bool = Query(default=True, description="Use fast single-query mode")) -> list[EvaluationsWithHydratedUsageRuns]:
#     try:
#         evaluations = await get_evaluations_with_usage_for_agent_version(agent_id, set_id, fast=fast)
#     except Exception as e:
#         logger.error(f"Error retrieving evaluations for version {agent_id}: {e}")
#         raise HTTPException(
#             status_code=500,
#             detail="Internal server error while retrieving evaluations. Please try again later."
#         )
    
#     return evaluations

# @router.get("/evaluation-run-logs", tags=["retrieval"], dependencies=[Depends(verify_request_public)])
# async def get_evaluation_run_logs(run_id: str) -> PlainTextResponse:
#     try:
#         logs = await db_get_evaluation_run_logs(run_id)
#     except Exception as e:
#         logger.error(f"Error retrieving logs for run {run_id}: {e}")
#         raise HTTPException(
#             status_code=500,
#             detail="Internal server error while retrieving logs for run. Please try again later."
#         )
    
#     return PlainTextResponse(content=logs)

# @router.get("/runs-for-evaluation", tags=["retrieval"], dependencies=[Depends(verify_request_public)])
# async def get_runs_for_evaluation(evaluation_id: str) -> list[EvaluationRun]:
#     try:
#         runs = await db_get_runs_for_evaluation(evaluation_id)
#     except Exception as e:
#         logger.error(f"Error retrieving runs for evaluation {evaluation_id}: {e}")
#         raise HTTPException(
#             status_code=500,
#             detail="Internal server error while retrieving runs for evaluation. Please try again later."
#         )
    
#     return runs



# @router.get("/latest-agent", tags=["retrieval"], dependencies=[Depends(verify_request_public)])
# async def get_latest_agent(miner_hotkey: str = None):
#     if not miner_hotkey:
#         raise HTTPException(
#             status_code=400,
#             detail="miner_hotkey must be provided"
#         )
    
#     latest_agent = await db_get_latest_agent(miner_hotkey=miner_hotkey)

#     if not latest_agent:
#         logger.info(f"Agent {miner_hotkey} was requested but not found in our database")
#         raise HTTPException(
#             status_code=404,
#             detail="Agent not found"
#         )
    
#     return latest_agent

# from models.evaluation import EvaluationStatus, Evaluation
# @router.get("/running-evaluations", tags=["retrieval"], dependencies=[Depends(verify_request_public)])
# async def get_running_evaluations() -> list[Evaluation]:
#     """
#     Gets a list of currently running evaluations to display on dashboard
#     """
#     from api.queries.evaluation import get_evaluations_by_status

#     evaluations = await get_evaluations_by_status(EvaluationStatus.running)

#     return evaluations

# @router.get("/top-agents", tags=["retrieval"], dependencies=[Depends(verify_request_public)])
# async def get_top_agents_old(num_agents: int = 3, search_term: Optional[str] = None, filter_for_open_user: bool = False, filter_for_registered_user: bool = False, filter_for_approved: bool = False) -> list[MinerAgentWithScores]:
#     """
#     Gets a list of current high score agents
#     """
#     if num_agents < 1:
#         raise HTTPException(
#             status_code=500,
#             detail="Must provide a fixed number of agents"
#         )
    
#     top_agents = await db_get_top_agents(num_agents=num_agents, search_term=search_term, filter_for_open_user=filter_for_open_user, filter_for_registered_user=filter_for_registered_user, filter_for_approved=filter_for_approved)

#     return top_agents

# @router.get("/miner-score-activity", tags=["retrieval"], dependencies=[Depends(verify_request_public)])
# async def miner_score_activity(set_id: Optional[int] = None):
#     """Gets miner submissions and top scores by hour for correlation analysis"""
#     return await db_get_miner_score_activity(set_id)

# @router.get("/queue-position-by-hotkey", tags=["retrieval"], dependencies=[Depends(verify_request_public)])
# async def get_queue_position(miner_hotkey: str) -> list[QueuePositionPerValidator]:
#     """
#     Gives a list of where an agent is in queue for every validator
#     """
#     positions = await get_queue_position_by_hotkey(miner_hotkey=miner_hotkey)

#     return positions

# @router.get("/agent-by-hotkey", tags=["retrieval"], dependencies=[Depends(verify_request_public)])
# async def agent_summary_by_hotkey(miner_hotkey: str) -> list[MinerAgentWithScores]:
#     """
#     Returns a list of every version of an agent submitted by a hotkey including its score. Used by the dashboard to render stats about the miner
#     """
#     agent_versions = await get_agent_summary_by_hotkey(miner_hotkey=miner_hotkey)
    
#     if agent_versions is None: 
#         raise HTTPException(
#             status_code=500,
#             detail="Error loading details for agent"
#         )

#     return agent_versions

# @router.get("/inferences-by-run", tags=["retrieval"], dependencies=[Depends(verify_request_public)])
# async def inferences_for_run(run_id: str) -> list[Inference]:
#     """
#     Returns a list of every version of an agent submitted by a hotkey including its score. Used by the dashboard to render stats about the miner
#     """
#     inferences = await get_inference_details_for_run(run_id=run_id)
    
#     if inferences is None: 
#         raise HTTPException(
#             status_code=500,
#             detail="Error loading inference calls for run"
#         )

#     return inferences

# @router.get("/agents-from-hotkey", tags=["retrieval"], dependencies=[Depends(verify_request_public)])
# async def get_agents_from_hotkey(miner_hotkey: str) -> list[MinerAgent]:
#     """
#     Returns a list of all agents for a given hotkey
#     """
#     try:
#         agents = await get_agents_by_hotkey(miner_hotkey=miner_hotkey)
#         return agents
#     except Exception as e:
#         logger.error(f"Error retrieving agents for hotkey {miner_hotkey}: {e}")
#         raise HTTPException(
#             status_code=500,
#             detail="Internal server error while retrieving agents"
#         )

# @router.get("/inference-provider-statistics", tags=["retrieval"], dependencies=[Depends(verify_request_public)])
# async def get_inference_provider_statistics(start_time: datetime, end_time: datetime) -> list[ProviderStatistics]:
#     """
#     Returns statistics on inference provider performance
#     """
#     try:
#         provider_statistics = await db_get_inference_provider_statistics(start_time=start_time, end_time=end_time)
#         return provider_statistics
#     except Exception as e:
#         logger.error(f"Error retrieving inferences for last {start_time} to {end_time}: {e}")
#         raise HTTPException(
#             status_code=500,
#             detail="Internal server error while retrieving inferences"
#         )


import uuid
import asyncio
from api.queries.agent import get_agents_in_queue, get_top_agents, get_agent_by_id, get_latest_agent_for_hotkey
from api.queries.evaluation import get_evaluations_for_agent_id
from api.queries.evaluation_run import get_all_evaluation_runs_in_evaluation_id
from models.evaluation import Evaluation, EvaluationWithRuns
from models.evaluation_set import EvaluationSetGroup
from models.agent import Agent, AgentScored, AgentStatus
from utils.s3 import download_text_file_from_s3
from api.src.endpoints.validator import get_all_connected_screener_ip_addresses

async def queue(
    stage: str
) -> list[Agent]:
    """
    Gets agents presently in queue in order.
    
    Args:
        stage: screener_1, screener_2, or validator.

    Returns:
        A list of Agent objects, sorted by their position in queue
    """
    return await get_agents_in_queue(EvaluationSetGroup(stage))

async def top_agents(
    number_of_agents: int = 15
) -> list[AgentScored]:
    """
    Returns the top agents for the latest problem set. All agents, including ones that have not been approved.
    """
    return await get_top_agents(
        number_of_agents=number_of_agents
    )

async def agent_by_id(agent_id: str) -> Agent:
    agent = await get_agent_by_id(agent_id=uuid.UUID(agent_id))
    
    if agent is None:
        raise HTTPException(
            status_code=404,
            detail="Agent not found"
        )

    return agent

async def agent_by_hotkey(miner_hotkey: str) -> Agent:
    """
    Returns the latest agent submitted by a hotkey
    """
    agent = await get_latest_agent_for_hotkey(miner_hotkey=miner_hotkey)
    
    if agent is None:
        raise HTTPException(
            status_code=404,
            detail="Agent not found"
        )

    return agent

async def evaluations_for_agent(agent_id: str) -> list[EvaluationWithRuns]:
    evaluations: list[Evaluation] = await get_evaluations_for_agent_id(agent_id=uuid.UUID(agent_id))
    
    runs_per_eval = await asyncio.gather(
        *[get_all_evaluation_runs_in_evaluation_id(evaluation_id=e.evaluation_id) for e in evaluations]
    )

    return [
        EvaluationWithRuns(evaluation=e, runs=runs)
        for e, runs in zip(evaluations, runs_per_eval)
    ]

from utils.s3 import download_text_file_from_s3
from api.src.endpoints.validator import get_all_connected_validator_ip_addresses

async def get_agent_code(agent_id: str, request: Request):
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

async def agent_scores_over_time(set_id: Optional[int] = None):
    """Gets agent scores over time for charting"""
    return await db_get_agent_scores_over_time(set_id)

async def network_statistics():
    """
    Gets network statistics for the dashboard
    """
    score_improvement, agents_created, top_score_value = await asyncio.gather(
        score_improvement_24_hrs(),
        agents_created_24_hrs(),
        top_score()
    )
    return {
        "score_improvement_24_hrs": score_improvement,
        "agents_created_24_hrs": agents_created,
        "top_score": top_score_value
    }

router = APIRouter()

routes = [
    ("/queue", queue),
    ("/top-agents", top_agents),
    ("/agent-by-id", agent_by_id),
    ("/agent-by-hotkey", agent_by_hotkey),
    ("/evaluations-for-agent", evaluations_for_agent),
    ("/agent-version-file", get_agent_code),
    ("/agent-scores-over-time", agent_scores_over_time),
    ("/network-statistics", network_statistics),
]

for path, endpoint in routes:
    router.add_api_route(
        path,
        endpoint,
        tags=["retrieval"],
        dependencies=[Depends(verify_request_public)],
        methods=["GET"]
    )
