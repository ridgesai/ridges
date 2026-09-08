import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from api.incentives import CurrentAllocations, get_current_allocations
from api.src.utils.leaderboard_cache import get_cached_leaderboard_rows
from models.agent import AgentStatus, PublicAgent
from models.evaluation_set import (
    EvaluationSet,
    EvaluationSetDetail,
    EvaluationSetDetailBenchmarkThreshold,
    EvaluationSetDetailEfficiency,
    EvaluationSetDetailEfficiencyAgent,
    EvaluationSetDetailPipelineStage,
    EvaluationSetDetailScores,
    EvaluationSetDetailSubmissions,
    EvaluationSetDetailTopAgent,
    EvaluationSetOverview,
    EvaluationSetOverviewPerformanceDistribution,
    EvaluationSetOverviewPerformanceImprovementPoint,
    EvaluationSetOverviewPreScreening,
    EvaluationSetOverviewScoreBucket,
    EvaluationSetProblem,
)
from queries.competition import (
    PublicEvaluationSetContext,
    get_competition_for_set,
    get_public_evaluation_set_context,
    resolve_compatibility_competition_set_id,
)
from queries.evaluation_set import (
    get_all_evaluation_set_problems_for_set_id,
    get_all_evaluation_sets,
    get_approved_agents_for_set,
    get_evaluation_set_leaderboard_summary,
    get_evaluation_set_performance_improvement,
    get_evaluation_set_pre_screening_distribution,
    get_evaluation_set_score_distribution,
    get_evaluation_set_score_stats,
    get_evaluation_set_submission_stats,
)
from utils.ttl import ttl_cache

router = APIRouter(tags=["evaluation-sets"])
logger = logging.getLogger(__name__)
CACHE_PAST_SET_DATA_TTL_SECONDS = 24 * 60 * 60  # Cache past set data for 24 hours, since it won't change
CACHE_LIVE_SET_OVERVIEW_TTL_SECONDS = 5 * 60


async def resolve_set_id(set_id: int) -> int:
    """Resolve the old -1 alias, or validate one public evaluation-set context."""
    if set_id == -1:
        resolved_set_id = await resolve_compatibility_competition_set_id()
    else:
        context = await get_public_evaluation_set_context(set_id)
        resolved_set_id = None if context is None else context.set_id
    if resolved_set_id is None:
        raise HTTPException(status_code=404, detail="No evaluation sets found.")
    return resolved_set_id


async def resolve_explicit_set_id(set_id: int) -> int:
    """Validate a new explicit route without applying compatibility fallback."""
    context = None if set_id == -1 else await get_public_evaluation_set_context(set_id)
    if context is None:
        raise HTTPException(status_code=404, detail="No evaluation sets found.")
    return context.set_id


async def _public_evaluation_set_or_404(set_id: int) -> PublicEvaluationSetContext:
    context = await get_public_evaluation_set_context(set_id)
    if context is None:
        raise HTTPException(status_code=404, detail="No evaluation sets found.")
    return context


@router.get("/")
async def evaluation_sets_list() -> list[EvaluationSet]:
    """Retrieve all evaluation sets."""
    return await get_all_evaluation_sets()


async def _build_problems(set_id: int) -> list[EvaluationSetProblem]:
    return await get_all_evaluation_set_problems_for_set_id(set_id)


_cached_build_live_problems = ttl_cache(ttl_seconds=CACHE_LIVE_SET_OVERVIEW_TTL_SECONDS)(_build_problems)
_cached_build_past_problems = ttl_cache(ttl_seconds=CACHE_PAST_SET_DATA_TTL_SECONDS)(_build_problems)


@router.get("/{set_id}/problems")
async def evaluation_set_problems(
    set_id: Annotated[int, Depends(resolve_explicit_set_id)],
) -> list[EvaluationSetProblem]:
    context = await _public_evaluation_set_or_404(set_id)
    if context.use_historical_cache:
        return await _cached_build_past_problems(set_id)
    return await _cached_build_live_problems(set_id)


#
# GET evaluation-sets/{set_id}/
#
async def _build_detail(set_id: int, required_validator_count: int | None) -> EvaluationSetDetail:
    def _pass_rate(count: int, total: int) -> float:
        """Calculates the pass rate for a given count of agents at a pipeline stage, relative to the total number of agents that entered the pipeline.

        Parameters
        ----------
        count : int
            The number of agents that passed the current pipeline stage.
        total : int
            The total number of agents that entered the evaluation pipeline.
        Returns
        -------
        float
            The pass rate as a decimal (e.g., 0.85 for 85% pass rate). Returns 0.0 if total is 0 to avoid division by zero.
        """
        return round(count / total, 4) if total > 0 else 0.0

    # 1. Fetch submission, score statistics, and competition info concurrently
    submission_row, score_row, competition_row, leaderboard_summary_row = await asyncio.gather(
        get_evaluation_set_submission_stats(set_id),
        get_evaluation_set_score_stats(set_id),
        get_competition_for_set(set_id),
        get_evaluation_set_leaderboard_summary(set_id, required_validator_count),
    )

    competition_name = competition_row["competition_name"] if competition_row else None
    competition_start_date = competition_row["competition_start_date"] if competition_row else None
    competition_end_date = competition_row["competition_end_date"] if competition_row else None

    # 3. Calculate pipeline stage counts and pass rates
    total = submission_row["total_agents"]
    pre_screening_count = total - submission_row["failed_at_pre_screening_count"]
    screener_1_count = pre_screening_count - submission_row["failed_at_screener_1_count"]
    screener_2_count = screener_1_count - submission_row["failed_at_screener_2_count"]
    validator_count = submission_row["finished_at_validator_count"]

    pipeline = [
        EvaluationSetDetailPipelineStage(
            stage="total",
            count=total,
            pass_rate=_pass_rate(total, total),
        ),
        EvaluationSetDetailPipelineStage(
            stage="pre_screening",
            count=pre_screening_count,
            pass_rate=_pass_rate(pre_screening_count, total),
        ),
        EvaluationSetDetailPipelineStage(
            stage="screener_1",
            count=screener_1_count,
            pass_rate=_pass_rate(screener_1_count, total),
        ),
        EvaluationSetDetailPipelineStage(
            stage="screener_2",
            count=screener_2_count,
            pass_rate=_pass_rate(screener_2_count, total),
        ),
        EvaluationSetDetailPipelineStage(
            stage="validator",
            count=validator_count,
            pass_rate=_pass_rate(validator_count, total),
        ),
        EvaluationSetDetailPipelineStage(
            stage="approved_emission",
            count=submission_row["approved_emission_count"],
            pass_rate=_pass_rate(submission_row["approved_emission_count"], total),
        ),
    ]

    submissions = EvaluationSetDetailSubmissions(
        total_agents=total,
        unique_miners=submission_row["unique_miners"],
        hardcoded_rejection_rate=(submission_row["failed_at_pre_screening_count"] / total if total > 0 else 0.0),
        approved_emission_count=submission_row["approved_emission_count"],
        pipeline=pipeline,
    )

    scores = EvaluationSetDetailScores(
        best=score_row["best"],
        average=score_row["average"],
        benchmark_thresholds=[
            EvaluationSetDetailBenchmarkThreshold(threshold=50, agents_above=score_row["above_50"]),
            EvaluationSetDetailBenchmarkThreshold(threshold=75, agents_above=score_row["above_75"]),
            EvaluationSetDetailBenchmarkThreshold(threshold=90, agents_above=score_row["above_90"]),
        ],
    )

    top_agent = (
        EvaluationSetDetailTopAgent(
            agent_id=leaderboard_summary_row["top_agent_id"],
            name=leaderboard_summary_row["top_agent_name"],
            version_num=leaderboard_summary_row["top_agent_version_num"],
            final_score=leaderboard_summary_row["top_agent_final_score"],
        )
        if leaderboard_summary_row["top_agent_id"] is not None
        else None
    )

    lowest_cost_value = leaderboard_summary_row["lowest_average_cost_usd_top_agents"]
    lowest_runtime_value = leaderboard_summary_row["lowest_average_runtime_seconds_top_agents"]

    efficiency = EvaluationSetDetailEfficiency(
        lowest_average_cost_usd_top_agents=(
            EvaluationSetDetailEfficiencyAgent(
                agent_id=leaderboard_summary_row["lowest_cost_agent_id"],
                value=lowest_cost_value,
            )
            if lowest_cost_value is not None
            else None
        ),
        lowest_average_runtime_seconds_top_agents=(
            EvaluationSetDetailEfficiencyAgent(
                agent_id=leaderboard_summary_row["lowest_runtime_agent_id"],
                value=lowest_runtime_value,
            )
            if lowest_runtime_value is not None
            else None
        ),
        average_agent_cost_usd=leaderboard_summary_row["average_agent_cost_usd"],
        average_agent_runtime_seconds=leaderboard_summary_row["average_agent_runtime_seconds"],
    )

    return EvaluationSetDetail(
        id=set_id,
        competition_name=competition_name,
        competition_start_date=competition_start_date,
        competition_end_date=competition_end_date,
        submissions=submissions,
        scores=scores,
        vs_previous_set=None,
        top_agent=top_agent,
        efficiency=efficiency,
    )


_cached_build_detail = ttl_cache(ttl_seconds=CACHE_PAST_SET_DATA_TTL_SECONDS)(_build_detail)


@router.get("/{set_id}")
async def evaluation_set_detail(
    set_id: Annotated[int, Depends(resolve_set_id)],
) -> EvaluationSetDetail:
    """Returns detailed information about a specific evaluation set, including:
    - Submission statistics at each stage of the evaluation pipeline
    - Score statistics (best, average, and benchmark thresholds)
    - Reserved nullable previous-competition comparison field
    """
    context = await _public_evaluation_set_or_404(set_id)
    if context.use_historical_cache:
        return await _cached_build_detail(set_id, context.required_validator_count)
    return await _build_detail(set_id, context.required_validator_count)


# GET evaluation-sets/{set_id}/overview
async def _build_overview(set_id: int, required_validator_count: int | None) -> EvaluationSetOverview:
    pre_screening_row, score_rows, improvement_rows = await asyncio.gather(
        get_evaluation_set_pre_screening_distribution(set_id),
        get_evaluation_set_score_distribution(set_id, required_validator_count),
        get_evaluation_set_performance_improvement(set_id),
    )

    counts_by_stage_and_bucket = {(row["stage"], row["bucket_index"]): row["agents"] for row in score_rows}

    def _score_buckets(stage: str) -> list[EvaluationSetOverviewScoreBucket]:
        return [
            EvaluationSetOverviewScoreBucket(
                min_score=bucket_index / 10,
                max_score=(bucket_index + 1) / 10,
                agents=counts_by_stage_and_bucket.get((stage, bucket_index), 0),
            )
            for bucket_index in range(10)
        ]

    return EvaluationSetOverview(
        set_id=set_id,
        performance_distribution=EvaluationSetOverviewPerformanceDistribution(
            pre_screening=EvaluationSetOverviewPreScreening(
                approved=pre_screening_row["approved"],
                rejected=pre_screening_row["rejected"],
                unresolved=pre_screening_row["unresolved"],
            ),
            screener_1=_score_buckets("screener_1"),
            screener_2=_score_buckets("screener_2"),
            validator=_score_buckets("validator"),
        ),
        performance_improvement=[
            EvaluationSetOverviewPerformanceImprovementPoint(
                date=row["date"],
                score=row["score"],
                cost=row["cost"],
                agent_id=row["agent_id"],
            )
            for row in improvement_rows
        ],
    )


_cached_build_live_overview = ttl_cache(ttl_seconds=CACHE_LIVE_SET_OVERVIEW_TTL_SECONDS)(_build_overview)
_cached_build_past_overview = ttl_cache(ttl_seconds=CACHE_PAST_SET_DATA_TTL_SECONDS)(_build_overview)


@router.get("/{set_id}/overview")
async def evaluation_set_overview(
    set_id: Annotated[int, Depends(resolve_set_id)],
) -> EvaluationSetOverview:
    context = await _public_evaluation_set_or_404(set_id)
    if context.use_historical_cache:
        return await _cached_build_past_overview(set_id, context.required_validator_count)
    return await _cached_build_live_overview(set_id, context.required_validator_count)


#
# GET evaluation-sets/{set_id}/leaderboard
#
@router.get("/{set_id}/leaderboard")
async def evaluation_set_leaderboard(
    set_id: Annotated[int, Depends(resolve_set_id)],
) -> list[PublicAgent]:
    """Retrieve the agent's leaderboard per evaluation set.

    Data for historical sets is cached for 24 hours while
    data for active sets is cached for 15 seconds.
    """
    context = await _public_evaluation_set_or_404(set_id)
    agent_rows = await get_cached_leaderboard_rows(
        set_id, context.required_validator_count, context.use_historical_cache
    )
    return [PublicAgent(**dict(row), set_id=set_id) for row in agent_rows]


#
# GET evaluation-sets/{set_id}/approved-agents
#


async def _build_approved_agents(
    set_id: int,
    required_validator_count: int | None,
) -> list[PublicAgent]:
    agent_rows = await get_approved_agents_for_set(set_id, required_validator_count)
    return [
        PublicAgent(
            **dict(row),
            status=AgentStatus.finished,
            set_id=set_id,
            approved=True,
        )
        for row in agent_rows
    ]


_cached_build_approved_agents = ttl_cache(ttl_seconds=CACHE_PAST_SET_DATA_TTL_SECONDS)(_build_approved_agents)


def _add_approved_agent_weights(
    agents: list[PublicAgent],
    allocations: CurrentAllocations | None,
) -> list[PublicAgent]:
    reward_weights = None if allocations is None else allocations.agent_weights
    return [
        agent.model_copy(
            update={
                "emission": None,
                "reward_weight": None if reward_weights is None else reward_weights.get(agent.agent_id, 0.0),
            }
        )
        for agent in agents
    ]


async def _safe_current_allocations() -> CurrentAllocations | None:
    try:
        return await get_current_allocations()
    except Exception:
        logger.exception("Could not compute current allocations for approved agents")
        return None


@router.get("/{set_id}/approved-agents")
async def evaluation_set_approved_agents(set_id: Annotated[int, Depends(resolve_set_id)]) -> list[PublicAgent]:
    context = await _public_evaluation_set_or_404(set_id)
    if context.use_historical_cache:
        agents = await _cached_build_approved_agents(set_id, context.required_validator_count)
        return _add_approved_agent_weights(agents, None)

    agents, allocations = await asyncio.gather(
        _build_approved_agents(set_id, context.required_validator_count),
        _safe_current_allocations(),
    )
    return _add_approved_agent_weights(agents, allocations)
