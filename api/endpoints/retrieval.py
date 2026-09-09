import asyncio
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import api.config as config
from api.competition_resolution import resolve_optional_public_competition
from api.src.utils.leaderboard_cache import get_cached_leaderboard_rows
from models.agent import (
    AgentStatus,
    PublicAgent,
)
from models.competition import CompetitionState
from models.evaluation import Evaluation, EvaluationWithRuns
from models.queue import QueueStage
from queries.agent import (
    get_agent_by_id,
    get_agent_score_and_set_id,
    get_agents_in_queue,
    get_all_public_agents_by_miner_hotkey,
    get_code_hiding_score_cutoff,
    get_latest_public_agent_for_miner_hotkey,
    get_public_agent_by_id,
    get_public_agent_rows_by_miner_coldkey,
    get_top_agents,
)
from queries.competition import (
    get_competition_policy,
    get_public_evaluation_set_context,
    resolve_compatibility_competition_set_id,
)
from queries.evaluation import get_approved_leader_ranking_for_set, get_evaluations_for_agent_id
from queries.evaluation_run import get_all_evaluation_runs_in_evaluation_id
from utils.incentives import calculate_time_multiplier
from utils.problem_alias import add_test_aliases, make_problem_alias
from utils.s3 import download_text_file_from_s3
from utils.ttl import ttl_cache

router = APIRouter()
CACHE_PAST_COMPETITION_TTL_SECONDS = 24 * 60 * 60


# /retrieval/queue?stage={pre_screening|screener_1|screener_2|validator}
async def _build_queue(stage: QueueStage, set_id: int) -> List[PublicAgent]:
    agents = await get_agents_in_queue(stage, set_id)
    return [PublicAgent(**agent.model_dump()) for agent in agents]


_cached_live_queue = ttl_cache(ttl_seconds=60)(_build_queue)
_cached_past_queue = ttl_cache(ttl_seconds=CACHE_PAST_COMPETITION_TTL_SECONDS)(_build_queue)


@router.get("/queue")
async def queue(stage: QueueStage, set_id: int | None = None) -> List[PublicAgent]:
    competition = await resolve_optional_public_competition(set_id)
    builder = _cached_past_queue if competition.state is CompetitionState.ended else _cached_live_queue
    return await builder(stage, competition.set_id)


# /retrieval/top-agents
async def _build_top_agents(set_id: int) -> List[PublicAgent]:
    return await get_top_agents(set_id=set_id, number_of_agents=50)


_cached_live_top_agents = ttl_cache(ttl_seconds=60)(_build_top_agents)
_cached_past_top_agents = ttl_cache(ttl_seconds=CACHE_PAST_COMPETITION_TTL_SECONDS)(_build_top_agents)


@router.get("/top-agents")
async def top_agents(set_id: int | None = None) -> List[PublicAgent]:
    competition = await resolve_optional_public_competition(set_id)
    builder = _cached_past_top_agents if competition.state is CompetitionState.ended else _cached_live_top_agents
    return await builder(competition.set_id)


# /retrieval/agent-by-id?agent_id=
@router.get("/agent-by-id")
async def agent_by_id(agent_id: UUID) -> PublicAgent:
    agent = await get_public_agent_by_id(agent_id)

    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent with ID {agent_id} not found")

    return agent


# /retrieval/agent-by-hotkey?miner_hotkey=
@router.get("/agent-by-hotkey")
async def agent_by_hotkey(miner_hotkey: str) -> PublicAgent:
    agent = await get_latest_public_agent_for_miner_hotkey(miner_hotkey=miner_hotkey)

    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent with miner hotkey {miner_hotkey} not found")

    return agent


# /retrieval/all-agents-by-hotkey?miner_hotkey=
@router.get("/all-agents-by-hotkey")
async def all_agents_by_hotkey(miner_hotkey: str, set_id: int | None = None) -> List[PublicAgent]:
    if set_id is None:
        resolved_set_id = None
    elif set_id == -1:
        resolved_set_id = await resolve_compatibility_competition_set_id()
        if resolved_set_id is None:
            raise HTTPException(status_code=404, detail="No live competition found")
    else:
        context = await get_public_evaluation_set_context(set_id)
        if context is None:
            raise HTTPException(status_code=404, detail=f"Evaluation set {set_id} not found")
        resolved_set_id = context.set_id

    agents = await get_all_public_agents_by_miner_hotkey(
        miner_hotkey=miner_hotkey,
        set_id=resolved_set_id,
    )
    return agents


# Leaderboard fields the per-set query owns. final_score and validator_count are
# included because the leaderboard adds tentative scores for agents still evaluating,
# which agent_scores has no row for yet.
_LEADERBOARD_FIELDS = (
    "rank",
    "final_score",
    "validator_count",
    "average_cost_usd",
    "average_runtime_seconds",
    "validator_hotkeys",
    "disqualified",
)


async def _leaderboard_rows_by_agent_id(set_id: int) -> dict[UUID, asyncpg.Record]:
    """The set's leaderboard, keyed by agent, or empty if the set is not public."""
    context = await get_public_evaluation_set_context(set_id)
    if context is None:
        return {}
    rows = await get_cached_leaderboard_rows(set_id, context.required_validator_count, context.use_historical_cache)
    return {row["agent_id"]: row for row in rows}


# /retrieval/agents-by-coldkey?miner_coldkey=
@router.get("/agents-by-coldkey")
async def agents_by_coldkey(miner_coldkey: str) -> dict[str, List[PublicAgent]]:
    """Returns the PublicAgent model shape, similar to /retrieval/all-agents-by-hotkey.
    Grouping: hotkeys sorted, agents newest-first within each.

    Each agent also carries the rank and validator metrics of its own competition,
    taken from that competition's leaderboard so the two always agree. Rank is a
    position among every agent in a set, so the leaderboards are fetched whole, one
    per set the coldkey competed in, and merged in by agent. Agents whose set has no
    public leaderboard keep the score agent_scores holds for them and no rank.
    """
    # 1. Retrieve all agents associated with the miner coldkey
    rows = [dict(row) for row in await get_public_agent_rows_by_miner_coldkey(miner_coldkey)]

    # 2. Retrieve all distinct set IDs
    set_ids = sorted({row["set_id"] for row in rows if row.get("set_id") is not None})

    # 3. Retrieve leaderboard information for each distinct set ID
    leaderboards = dict(
        zip(
            set_ids,
            await asyncio.gather(*(_leaderboard_rows_by_agent_id(set_id) for set_id in set_ids)),
        )
    )

    # 4. Build response object
    grouped: dict[str, List[PublicAgent]] = {}
    for row in rows:
        leaderboard_row = leaderboards.get(row.get("set_id"), {}).get(row["agent_id"])
        if leaderboard_row is not None:
            row.update({field: leaderboard_row[field] for field in _LEADERBOARD_FIELDS})
        agent = PublicAgent(**row)
        grouped.setdefault(agent.miner_hotkey, []).append(agent)
    return grouped


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


async def _code_hiding_score_cutoff(set_id: int) -> Optional[float]:
    return await get_code_hiding_score_cutoff(
        top_agent_count=config.CODE_HIDE_TOP_AGENT_COUNT,
        top_score_count=config.CODE_HIDE_TOP_SCORE_COUNT,
        set_id=set_id,
    )


_cached_code_hiding_score_cutoff = ttl_cache(ttl_seconds=60)(_code_hiding_score_cutoff)

# Past-competition scores never change once the competition ends, so a long TTL is safe and
# avoids recomputing the cutoff on every request for popular old agents.
_cached_past_code_hiding_score_cutoff = ttl_cache(ttl_seconds=24 * 60 * 60)(_code_hiding_score_cutoff)


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
        AgentStatus.failed_screening_1,
        AgentStatus.screening_2,
        AgentStatus.failed_screening_2,
        AgentStatus.evaluating,
    ]
    if agent.status in hidden_statuses:
        raise HTTPException(status_code=403, detail=f"Agent {agent.agent_id} is still being screened/evaluated")

    score_and_set = await get_agent_score_and_set_id(agent_id)
    if score_and_set is not None:
        set_id, candidate_score, membership_set_id = score_and_set
        if membership_set_id is not None and membership_set_id != set_id:
            raise HTTPException(status_code=403, detail="Agent code is hidden because competition data conflicts")

        context = await get_public_evaluation_set_context(set_id)
        cutoff = (
            await _cached_past_code_hiding_score_cutoff(set_id)
            if context is not None and context.use_historical_cache
            else await _cached_code_hiding_score_cutoff(set_id)
        )
        if cutoff is not None and candidate_score >= cutoff:
            raise HTTPException(status_code=403, detail="Agent code is hidden for top agents")

    return await download_text_file_from_s3(f"{agent_id}/agent.py")


# /retrieval/network-statistics
class NetworkStatisticsResponse(BaseModel):
    set_id: int
    top_score: Optional[float]
    top_cost: Optional[float]
    perf_threshold: Optional[float]
    cost_threshold: Optional[float]
    last_approval: Optional[datetime]
    time_multiplier: Optional[float]


async def _build_network_statistics(set_id: int) -> NetworkStatisticsResponse:
    policy = await get_competition_policy(set_id)
    leader = (
        None
        if policy is None
        else await get_approved_leader_ranking_for_set(
            set_id,
            required_validator_count=policy.required_validator_count,
        )
    )

    time_multiplier = None
    if policy is not None:
        time_multiplier = 1.0
    if policy is not None and leader is not None and leader.approved_at is not None:
        observed_at = leader.observed_at or datetime.now(timezone.utc)
        elapsed_hours = max(0.0, (observed_at - leader.approved_at).total_seconds() / 3600)
        time_multiplier = calculate_time_multiplier(
            elapsed_hours=elapsed_hours,
            scale_hours=policy.incentive_time_multiplier_scale_hours,
        )

    return NetworkStatisticsResponse(
        set_id=set_id,
        top_score=None if leader is None else leader.final_score,
        top_cost=None if leader is None else leader.avg_cost_usd,
        perf_threshold=None if policy is None else policy.incentive_performance_threshold,
        cost_threshold=None if policy is None else policy.incentive_cost_threshold,
        last_approval=None if leader is None else leader.approved_at,
        time_multiplier=time_multiplier,
    )


_cached_live_network_statistics = ttl_cache(ttl_seconds=60)(_build_network_statistics)
_cached_past_network_statistics = ttl_cache(ttl_seconds=CACHE_PAST_COMPETITION_TTL_SECONDS)(_build_network_statistics)


@router.get("/network-statistics")
async def network_statistics(set_id: int | None = None) -> NetworkStatisticsResponse:
    competition = await resolve_optional_public_competition(set_id)
    builder = (
        _cached_past_network_statistics
        if competition.state is CompetitionState.ended
        else _cached_live_network_statistics
    )
    return await builder(competition.set_id)
