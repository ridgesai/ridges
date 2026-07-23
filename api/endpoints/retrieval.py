import asyncio
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import api.config as config
from models.agent import Agent, AgentScored, AgentStatus, BenchmarkAgentScored, PossiblyBenchmarkAgent
from models.evaluation import Evaluation, EvaluationWithRuns
from models.queue import QueueStage
from queries.agent import (
    get_agent_by_id,
    get_agents_in_queue,
    get_all_agents_by_miner_hotkey,
    get_benchmark_agents,
    get_code_hiding_candidate_score,
    get_code_hiding_score_cutoff,
    get_latest_agent_for_miner_hotkey,
    get_possibly_benchmark_agent_by_id,
    get_top_agents,
)
from queries.evaluation import get_approved_leader_ranking_for_set, get_evaluations_for_agent_id
from queries.evaluation_run import get_all_evaluation_runs_in_evaluation_id
from queries.evaluation_set import get_latest_set_id
from queries.statistics import (
    PerfectlySolvedOverTime,
    ProblemSetCreationTime,
    TopScoreOverTime,
    get_perfectly_solved_over_time,
    get_problem_set_creation_times,
    get_top_scores_over_time,
)
from utils.incentives import calculate_time_multiplier
from utils.problem_alias import add_test_aliases, make_problem_alias
from utils.s3 import download_text_file_from_s3
from utils.ttl import ttl_cache

router = APIRouter()


# /retrieval/queue?stage={pre_screening|screener_1|screener_2|validator}
@router.get("/queue")
@ttl_cache(ttl_seconds=60)  # 1 minute
async def queue(stage: QueueStage) -> List[Agent]:
    return await get_agents_in_queue(stage)


# /retrieval/top-agents
@router.get("/top-agents")
@ttl_cache(ttl_seconds=60)  # 1 minute
async def top_agents() -> List[AgentScored]:
    return await get_top_agents(number_of_agents=50)


# /retrieval/benchmark-agents
@router.get("/benchmark-agents")
@ttl_cache(ttl_seconds=10 * 60)  # 10 minutes
async def benchmark_agents() -> List[BenchmarkAgentScored]:
    return await get_benchmark_agents()


# /retrieval/agent-by-id?agent_id=
@router.get("/agent-by-id")
async def agent_by_id(agent_id: UUID) -> PossiblyBenchmarkAgent:
    agent = await get_possibly_benchmark_agent_by_id(agent_id)

    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent with ID {agent_id} not found")

    return agent


# /retrieval/agent-by-hotkey?miner_hotkey=
@router.get("/agent-by-hotkey")
async def agent_by_hotkey(miner_hotkey: str) -> Agent:
    agent = await get_latest_agent_for_miner_hotkey(miner_hotkey=miner_hotkey)

    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent with miner hotkey {miner_hotkey} not found")

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

    enriched_runs = [
        [
            run.model_copy(
                update={
                    "problem_alias": make_problem_alias(run.problem_name, run.benchmark_family),
                    "test_results": add_test_aliases(
                        run.test_results,
                        problem_name=run.problem_name,
                        benchmark_family=run.benchmark_family,
                    ),
                }
            )
            for run in runs
        ]
        for runs in runs_per_eval
    ]

    return [EvaluationWithRuns(**e.model_dump(), runs=runs) for e, runs in zip(evaluations, enriched_runs)]


_get_latest_set_id = ttl_cache(ttl_seconds=30)(get_latest_set_id)


async def _code_hiding_score_cutoff(set_id: int) -> Optional[float]:
    return await get_code_hiding_score_cutoff(
        top_agent_count=config.CODE_HIDE_TOP_AGENT_COUNT,
        top_score_count=config.CODE_HIDE_TOP_SCORE_COUNT,
        set_id=set_id,
    )


_cached_code_hiding_score_cutoff = ttl_cache(ttl_seconds=60)(_code_hiding_score_cutoff)


# /retrieval/agent-code?agent_id=
@router.get("/agent-code")
async def agent_code(agent_id: UUID) -> str:
    agent = await get_agent_by_id(agent_id=agent_id)

    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent with ID {agent_id} not found")

    hidden_statuses = [
        AgentStatus.pre_screening,
        AgentStatus.failed_pre_screening,
        AgentStatus.pre_screening_needs_review,
        AgentStatus.screening_1,
        AgentStatus.screening_2,
        AgentStatus.evaluating,
    ]
    if agent.status in hidden_statuses:
        raise HTTPException(status_code=403, detail=f"Agent {agent.agent_id} is still being screened/evaluated")

    latest_set_id = await _get_latest_set_id()
    if latest_set_id is not None:
        candidate_score = await get_code_hiding_candidate_score(agent_id=agent_id, set_id=latest_set_id)
        if candidate_score is not None:
            cutoff = await _cached_code_hiding_score_cutoff(latest_set_id)
            if cutoff is not None and candidate_score >= cutoff:
                raise HTTPException(status_code=403, detail="Agent code is hidden for top agents")

    return await download_text_file_from_s3(f"{agent_id}/agent.py")


# /retrieval/top-scores-over-time
@router.get("/top-scores-over-time")
@ttl_cache(ttl_seconds=60 * 15)  # 15 minutes
async def top_scores_over_time() -> List[TopScoreOverTime]:
    return await get_top_scores_over_time()


# /retrieval/perfectly-solved-over-time
class PerfectlySolvedOverTimeResponse(BaseModel):
    perfectly_solved_over_times: List[PerfectlySolvedOverTime]
    problem_set_creation_times: List[ProblemSetCreationTime]


@router.get("/perfectly-solved-over-time")
@ttl_cache(ttl_seconds=60 * 15)  # 15 minutes
async def perfectly_solved_over_time() -> PerfectlySolvedOverTimeResponse:
    return PerfectlySolvedOverTimeResponse(
        perfectly_solved_over_times=await get_perfectly_solved_over_time(),
        problem_set_creation_times=await get_problem_set_creation_times(),
    )


# /retrieval/network-statistics
class NetworkStatisticsResponse(BaseModel):
    top_score: Optional[float]
    top_cost: Optional[float]
    perf_threshold: float
    cost_threshold: float
    last_approval: Optional[datetime]
    time_multiplier: float


@router.get("/network-statistics")
@ttl_cache(ttl_seconds=60)
async def network_statistics() -> NetworkStatisticsResponse:
    latest_set_id = await get_latest_set_id()
    leader = (
        None
        if latest_set_id is None
        else await get_approved_leader_ranking_for_set(
            latest_set_id,
            required_validator_count=config.NUM_EVALS_PER_AGENT,
        )
    )

    time_multiplier = 1.0
    if leader is not None and leader.approved_at is not None:
        observed_at = leader.observed_at or datetime.now(timezone.utc)
        elapsed_hours = max(0.0, (observed_at - leader.approved_at).total_seconds() / 3600)
        time_multiplier = calculate_time_multiplier(
            elapsed_hours=elapsed_hours,
            half_life_hours=config.INCENTIVE_TIME_MULTIPLIER_HALF_LIFE_HOURS,
            maximum=config.INCENTIVE_TIME_MULTIPLIER_MAX,
        )

    return NetworkStatisticsResponse(
        top_score=None if leader is None else leader.final_score,
        top_cost=None if leader is None else leader.avg_cost_usd,
        perf_threshold=config.INCENTIVE_PERFORMANCE_THRESHOLD,
        cost_threshold=config.INCENTIVE_COST_THRESHOLD,
        last_approval=None if leader is None else leader.approved_at,
        time_multiplier=time_multiplier,
    )
