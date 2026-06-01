import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from models.evaluation_set import (
    ApprovedAgent,
    EvaluationSet,
    EvaluationSetDetail,
    EvaluationSetDetailAgent,
    EvaluationSetDetailBenchmarkThreshold,
    EvaluationSetDetailEfficiency,
    EvaluationSetDetailPipelineStage,
    EvaluationSetDetailScores,
    EvaluationSetDetailSubmissions,
    EvaluationSetDetailTopAgent,
    EvaluationSetDetailVsPreviousSet,
    EvaluationSetProblem,
)
from queries.competition import get_competition_for_set
from queries.evaluation_set import (
    get_all_evaluation_set_problems_for_set_id,
    get_all_evaluation_sets,
    get_approved_agents_for_set,
    get_evaluation_set_leaderboard_agents,
    get_evaluation_set_leaderboard_summary,
    get_evaluation_set_score_stats,
    get_evaluation_set_submission_stats,
    get_latest_set_id,
    get_set_created_at,
)
from utils.bittensor import subtensor_client

router = APIRouter(tags=["evaluation-sets"])


async def resolve_set_id(set_id: int) -> int:
    """Parse the set_id path parameter, resolving -1 to the latest set ID. Validates that the resolved set ID exists.

    Parameters
    ----------
    set_id : int
        Original set_id from the path parameter, where -1 indicates the latest set.

    Returns
    -------
    int
        Parsed and validated set ID, with -1 resolved to the latest set ID.
    """
    if set_id < -1:
        raise HTTPException(status_code=404, detail="No evaluation sets found.")

    resolved_set_id = None
    if set_id == -1:
        resolved_set_id = await get_latest_set_id()
    else:
        if await get_set_created_at(set_id) is not None:
            resolved_set_id = set_id

    if resolved_set_id is None:
        raise HTTPException(status_code=404, detail="No evaluation sets found.")
    return resolved_set_id


@router.get("/")
async def evaluation_sets_list() -> list[EvaluationSet]:
    """Retrieve all evaluation sets."""
    return await get_all_evaluation_sets()


# /evaluation-sets/all-latest-set-problems
@router.get("/all-latest-set-problems")
async def evaluation_sets_all_latest_set_problems() -> list[EvaluationSetProblem]:
    latest_set_id = await get_latest_set_id()
    if latest_set_id is None:
        return []
    return await get_all_evaluation_set_problems_for_set_id(latest_set_id)


@router.get("/{set_id}")
async def evaluation_set_detail(
    set_id: Annotated[int, Depends(resolve_set_id)],
) -> EvaluationSetDetail:
    """Returns detailed information about a specific evaluation set, including:
    - Submission statistics at each stage of the evaluation pipeline
    - Score statistics (best, average, and benchmark thresholds)
    - Comparison against the previous evaluation set's best score (if available)
    """

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
        get_evaluation_set_leaderboard_summary(set_id),
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

    # 4. Construct the response model
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

    vs_previous_set = None
    if score_row["best"] is not None and score_row["prev_best_score"] is not None:
        delta = score_row["best"] - score_row["prev_best_score"]
        top_score_delta = f"+{delta:.2f}" if delta >= 0 else f"{delta:.2f}"
        vs_previous_set = EvaluationSetDetailVsPreviousSet(
            top_score_delta=top_score_delta,
            agents_beating_previous_best=score_row["agents_beating_previous_best"],
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

    efficiency = EvaluationSetDetailEfficiency(
        lowest_average_cost_usd_top_agents=leaderboard_summary_row["lowest_average_cost_usd_top_agents"],
        lowest_average_runtime_seconds_top_agents=leaderboard_summary_row["lowest_average_runtime_seconds_top_agents"],
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
        vs_previous_set=vs_previous_set,
        top_agent=top_agent,
        efficiency=efficiency,
    )


@router.get("/{set_id}/approved-agents")
async def evaluation_set_approved_agents(set_id: Annotated[int, Depends(resolve_set_id)]) -> list[ApprovedAgent]:
    agent_rows = await get_approved_agents_for_set(set_id)

    emission_results = await asyncio.gather(
        *[subtensor_client.get_emission(row["miner_hotkey"]) for row in agent_rows],
        return_exceptions=True,
    )

    return [
        ApprovedAgent(
            id=row["agent_id"],
            miner_hotkey=row["miner_hotkey"],
            name=row["name"],
            version_num=row["version_num"],
            created_at=row["created_at"],
            final_score=row["final_score"],
            emission=emission if isinstance(emission, float) else 0.0,
        )
        for row, emission in zip(agent_rows, emission_results)
    ]


@router.get("/{set_id}/leaderboard")
async def evaluation_set_leaderboard(
    set_id: Annotated[int, Depends(resolve_set_id)],
) -> list[EvaluationSetDetailAgent]:
    agent_rows = await get_evaluation_set_leaderboard_agents(set_id)
    return [EvaluationSetDetailAgent(**dict(row)) for row in agent_rows]
