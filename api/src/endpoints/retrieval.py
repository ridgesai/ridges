from fastapi import APIRouter, Depends, HTTPException, Query, Request
from typing import Optional, Any
from fastapi.responses import StreamingResponse, PlainTextResponse
import utils.logger as logger
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone

from api.config import MINER_AGENT_UPLOAD_RATE_LIMIT_SECONDS
from api.src.utils.auth import verify_request_public
from api.src.backend.entities import EvaluationRun, MinerAgent, EvaluationsWithHydratedRuns, Inference, EvaluationsWithHydratedUsageRuns, MinerAgentWithScores, ScreenerQueueByStage
from api.src.backend.queries.agents import get_latest_agent as db_get_latest_agent, get_agent_by_agent_id, get_agents_by_hotkey
from api.src.backend.queries.evaluations import get_evaluation_by_evaluation_id, get_evaluations_for_agent_version, get_evaluations_with_usage_for_agent_version
from api.src.backend.queries.evaluations import get_queue_info as db_get_queue_info
from api.src.backend.queries.evaluation_runs import get_runs_for_evaluation as db_get_runs_for_evaluation, get_evaluation_run_logs as db_get_evaluation_run_logs
from api.src.backend.queries.bench_evaluation_runs import get_runs_for_benchmark_evaluation as db_get_runs_for_benchmark_evaluation
from api.src.backend.queries.statistics import get_24_hour_statistics, get_currently_running_evaluations, RunningEvaluation, get_agent_summary_by_hotkey
from api.src.backend.queries.statistics import get_top_agents as db_get_top_agents, get_queue_position_by_hotkey, QueuePositionPerValidator, get_inference_details_for_run
from api.src.backend.queries.statistics import get_agent_scores_over_time as db_get_agent_scores_over_time, get_miner_score_activity as db_get_miner_score_activity
from api.queries.evaluation_set import get_latest_set_id
from api.src.backend.entities import ProviderStatistics
from api.src.backend.queries.inference import get_inference_provider_statistics as db_get_inference_provider_statistics
from api.src.backend.queries.open_users import get_emission_dispersed_to_open_user as db_get_emission_dispersed_to_open_user, get_all_transactions as db_get_all_transactions, get_all_treasury_hotkeys as db_get_all_treasury_hotkeys
from api.src.backend.queries.agents import get_all_approved_agent_ids as db_get_all_approved_agent_ids
from api.src.backend.queries.open_users import get_total_dispersed_by_treasury_hotkeys as db_get_total_dispersed_by_treasury_hotkeys


load_dotenv()

router = APIRouter()

SCREENER_IP_LIST = [
    "3.89.93.137", # 1-1
    "35.174.155.46", # 1-2
    "3.82.227.252", # 1-3
    "34.207.95.225", # 1-4
    "44.204.233.125", # 1-5
    "13.221.244.67", # 1-6
    "13.221.159.150", # 1-7
    "44.212.65.240", # 1-8
    "184.73.11.250", # 2-1
    "18.212.35.108", # 2-2
    "3.91.231.29", # 2-3
]

@router.get("/agent-version-file", tags=["retrieval"], dependencies=[Depends(verify_request_public)])
async def get_agent_code(agent_id: str, request: Request, return_as_text: bool = False):


    # TODO ADAM: i will rewrite this. shit probably doesn't even work rn


    agent_version = await get_agent_by_agent_id(agent_id=agent_id)
    
    if not agent_version:
        logger.info(f"File for agent version {agent_id} was requested but not found in our database")
        raise HTTPException(
            status_code=404, 
            detail="The requested agent version was not found. Are you sure you have the correct version ID?"
        )
    
    # TODO: Code hiding should not be implemented like this, we should have the IP list be dynamically generated from the list of currently connected screeners.
    # If status is screening, verify that it is a screener requesting
    if "screening" in agent_version.status:
        # Get client IP address
        client_ip = request.client.host
        
        # Check if IP is in whitelist (add your allowed IPs to SCREENER_IP_LIST)
        if client_ip not in SCREENER_IP_LIST:
            logger.warning(f"Unauthorized IP {client_ip} attempted to access agent code for version {agent_id}")
            raise HTTPException(
                status_code=403,
                detail="Access denied: IP not authorized"
            )
    
    if return_as_text:
        try:
            text = await s3_manager.get_file_text(f"{agent_id}/agent.py")
        except Exception as e:
            logger.error(f"Error retrieving agent version code from S3 for version {agent_id}: {e}")
            raise HTTPException(
                status_code=500,
                detail="Internal server error while retrieving agent version code. Please try again later."
            )
        
        return text

    try:
        agent_object = await s3_manager.get_file_object(f"{agent_id}/agent.py")
    except Exception as e:
        logger.error(f"Error retrieving agent version file from S3 for version {agent_id}: {e}")
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

@router.get("/connected-validators", tags=["retrieval"], dependencies=[Depends(verify_request_public)])
async def get_connected_validators():
    """
    Returns a list of all connected validators and screener validators
    """
    raise NotImplementedError("WE REMOVED THIS FORSAKEN FUNCTION DO NOT CALL IT")

@router.get("/queue-info", tags=["retrieval"], dependencies=[Depends(verify_request_public)])
async def get_queue_info(agent_id: str):
    try:
        queue_info = await db_get_queue_info(agent_id)
    except Exception as e:
        logger.error(f"Error retrieving queue info for version {agent_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving queue info. Please try again later."
        )
    
    return queue_info

@router.get("/evaluations", tags=["retrieval"], dependencies=[Depends(verify_request_public)])
async def get_evaluations(agent_id: str, set_id: Optional[int] = None) -> list[EvaluationsWithHydratedRuns]:
    try:
        # If no set_id provided, use the latest set_id
        if set_id is None:
            set_id = await get_latest_set_id()
        
        evaluations = await get_evaluations_for_agent_version(agent_id, set_id)
    except Exception as e:
        logger.error(f"Error retrieving evaluations for version {agent_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving evaluations. Please try again later."
        )
    
    return evaluations

@router.get("/evaluations-with-usage", tags=["retrieval"], dependencies=[Depends(verify_request_public)])
async def get_evaluations_with_usage(agent_id: str, set_id: Optional[int] = None, fast: bool = Query(default=True, description="Use fast single-query mode")) -> list[EvaluationsWithHydratedUsageRuns]:
    try:
        evaluations = await get_evaluations_with_usage_for_agent_version(agent_id, set_id, fast=fast)
    except Exception as e:
        logger.error(f"Error retrieving evaluations for version {agent_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving evaluations. Please try again later."
        )
    
    return evaluations

@router.get("/screening-evaluations", tags=["retrieval"], dependencies=[Depends(verify_request_public)])
async def get_screening_evaluations(agent_id: str, stage: int = Query(description="Screening stage (1 or 2)"), set_id: Optional[int] = None) -> list[EvaluationsWithHydratedRuns]:
    """Get screening evaluations for an agent version filtered by stage"""
    try:
        # Validate stage parameter
        if stage not in [1, 2]:
            raise HTTPException(
                status_code=400,
                detail="Stage must be 1 or 2"
            )
        
        # If no set_id provided, use the latest set_id
        if set_id is None:
            set_id = await get_latest_set_id()
        
        evaluations = await get_evaluations_for_agent_version(agent_id, set_id)
        
        # Filter to only screening evaluations (screener- or i-0 prefixed validator hotkeys)
        screening_evaluations = [
            eval for eval in evaluations 
            if eval.validator_hotkey.startswith('screener-') or eval.validator_hotkey.startswith('i-0')
        ]
        
        # Filter by stage
        # Stage 1: screener-1 or similar patterns
        # Stage 2: screener-2 or similar patterns
        stage_filtered = []
        for eval in screening_evaluations:
            hotkey = eval.validator_hotkey
            if stage == 1 and ('screener-1' in hotkey or 'stage-1' in hotkey or (hotkey.startswith('i-0') and '1' in hotkey)):
                stage_filtered.append(eval)
            elif stage == 2 and ('screener-2' in hotkey or 'stage-2' in hotkey or (hotkey.startswith('i-0') and '2' in hotkey)):
                stage_filtered.append(eval)
        screening_evaluations = stage_filtered
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving screening evaluations for version {agent_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving screening evaluations. Please try again later."
        )
    
    return screening_evaluations

@router.get("/evaluation-run-logs", tags=["retrieval"], dependencies=[Depends(verify_request_public)])
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

@router.get("/runs-for-evaluation", tags=["retrieval"], dependencies=[Depends(verify_request_public)])
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

@router.get("/top-benchmark-agent-evaluations", tags=["retrieval"], dependencies=[Depends(verify_request_public)])
async def get_top_benchmark_agent_evaluations() -> list[EvaluationRun]:
    """
    Get evaluation runs for top benchmark agents from the bench_evaluation_runs table.
    Uses a hardcoded evaluation_id for fetching the specific benchmark evaluation data.
    """
    # TODO: Replace with actual evaluation_id when provided
    hardcoded_evaluation_id = "0501b200-0b4f-48ed-a163-cc0a5691b34f"
    
    try:
        runs = await db_get_runs_for_benchmark_evaluation(hardcoded_evaluation_id)
    except Exception as e:
        logger.error(f"Error retrieving benchmark evaluation runs for evaluation {hardcoded_evaluation_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while retrieving benchmark evaluation runs. Please try again later."
        )
    
    return runs

@router.get("/latest-agent", tags=["retrieval"], dependencies=[Depends(verify_request_public)])
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

@router.get("/network-stats", tags=["retrieval"], dependencies=[Depends(verify_request_public)])
async def get_network_stats():
    """
    Gets statistics on the number of agents, score changes, etc. Primarily ingested by the dashboard
    """
    statistics_24_hrs = await get_24_hour_statistics()

    return statistics_24_hrs

from models.evaluation import EvaluationStatus, Evaluation
@router.get("/running-evaluations", tags=["retrieval"], dependencies=[Depends(verify_request_public)])
async def get_running_evaluations() -> list[Evaluation]:
    """
    Gets a list of currently running evaluations to display on dashboard
    """
    from api.queries.evaluation import get_evaluations_by_status

    evaluations = await get_evaluations_by_status(EvaluationStatus.running)

    return evaluations

@router.get("/top-agents", tags=["retrieval"], dependencies=[Depends(verify_request_public)])
async def get_top_agents_old(num_agents: int = 3, search_term: Optional[str] = None, filter_for_open_user: bool = False, filter_for_registered_user: bool = False, filter_for_approved: bool = False) -> list[MinerAgentWithScores]:
    """
    Gets a list of current high score agents
    """
    if num_agents < 1:
        raise HTTPException(
            status_code=500,
            detail="Must provide a fixed number of agents"
        )
    
    top_agents = await db_get_top_agents(num_agents=num_agents, search_term=search_term, filter_for_open_user=filter_for_open_user, filter_for_registered_user=filter_for_registered_user, filter_for_approved=filter_for_approved)

    return top_agents

@router.get("/agent-scores-over-time", tags=["retrieval"], dependencies=[Depends(verify_request_public)])
async def agent_scores_over_time(set_id: Optional[int] = None):
    """Gets agent scores over time for charting"""
    return await db_get_agent_scores_over_time(set_id)

@router.get("/miner-score-activity", tags=["retrieval"], dependencies=[Depends(verify_request_public)])
async def miner_score_activity(set_id: Optional[int] = None):
    """Gets miner submissions and top scores by hour for correlation analysis"""
    return await db_get_miner_score_activity(set_id)

@router.get("/queue-position-by-hotkey", tags=["retrieval"], dependencies=[Depends(verify_request_public)])
async def get_queue_position(miner_hotkey: str) -> list[QueuePositionPerValidator]:
    """
    Gives a list of where an agent is in queue for every validator
    """
    positions = await get_queue_position_by_hotkey(miner_hotkey=miner_hotkey)

    return positions

@router.get("/agent-by-hotkey", tags=["retrieval"], dependencies=[Depends(verify_request_public)])
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

@router.get("/inferences-by-run", tags=["retrieval"], dependencies=[Depends(verify_request_public)])
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

@router.get("/agents-from-hotkey", tags=["retrieval"], dependencies=[Depends(verify_request_public)])
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

@router.get("/inference-provider-statistics", tags=["retrieval"], dependencies=[Depends(verify_request_public)])
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

@router.get("/agent-scratch", tags=["retrieval"], dependencies=[Depends(verify_request_public)])
async def shak_scratchpad() -> Any:
    # Queue
    # agent = await get_agents_in_queue(EvaluationSetGroup.screener_1)
    # return agent

    # top agents 
    # agents = await x()
    # return agents

    # Connected validators and what theyre doing 
    pass

import uuid
import asyncio
from api.queries.agent import get_agents_in_queue, get_top_agents, get_agent_by_id, get_latest_agent_for_hotkey
from api.queries.evaluation import get_evaluations_for_agent_id, get_all_evaluation_runs_in_evaluation_id
from models.evaluation import EvaluationStatus, Evaluation, EvaluationWithRuns
from models.evaluation_set import EvaluationSetGroup
from models.agent import Agent, AgentScored

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

async def network_statistics():
    """
    """
    pass 

async def evaluation_with_runs():
    """
    """
    pass

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

async def inference_statistics():
    pass

async def weight_receiving_agent():
    pass

router = APIRouter()

routes = [
    ("/queue", queue),
    ("/top-agents", top_agents),
    ("/agent-by-id", agent_by_id),
    ("/agent-by-hotkey", agent_by_hotkey),
    ("/evaluations-for-agent", evaluations_for_agent)

    # ("/agent-version-file", get_agent_code), 
    # ("/connected-validators", get_connected_validators), 
    # ("/evaluations", get_evaluations),
    # ("/screening-evaluations", get_screening_evaluations),
    # ("/runs-for-evaluation", get_runs_for_evaluation),
    # ("/network-stats", get_network_stats),
    # ("/running-evaluations", get_running_evaluations),
    # ("/top-agents", get_top_agents),
    # ("/queue-position-by-hotkey", get_queue_position),
    # ("/inferences-by-run", inferences_for_run),
    # ("/agent-scores-over-time", agent_scores_over_time),
    # ("/miner-score-activity", miner_score_activity),
    # ("/agents-from-hotkey", get_agents_from_hotkey),    
    # ("/inference-provider-statistics", get_inference_provider_statistics),
    # ("/agent-scratch", shak_scratchpad)
]

for path, endpoint in routes:
    router.add_api_route(
        path,
        endpoint,
        tags=["retrieval"],
        dependencies=[Depends(verify_request_public)],
        methods=["GET"]
    )
