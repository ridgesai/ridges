import asyncio

from fastapi import APIRouter, HTTPException

from models.evaluation_set import (
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
    get_evaluation_set_leaderboard_agents,
    get_evaluation_set_leaderboard_summary,
    get_evaluation_set_score_stats,
    get_evaluation_set_submission_stats,
    get_latest_set_id,
    get_set_created_at,
)

router = APIRouter(tags=["evaluation-sets"])


async def _resolve_set_id(set_id: int) -> int:
    if set_id != -1:
        return set_id

    resolved = await get_latest_set_id()
    if resolved is None:
        raise HTTPException(status_code=404, detail="No evaluation sets found.")
    return resolved


@router.get("/")
async def evaluation_sets_list() -> list[EvaluationSet]:
    return await get_all_evaluation_sets()


# /evaluation-sets/all-latest-set-problems
@router.get("/all-latest-set-problems")
async def evaluation_sets_all_latest_set_problems() -> list[EvaluationSetProblem]:
    latest_set_id = await get_latest_set_id()
    if latest_set_id is None:
        return []
    return await get_all_evaluation_set_problems_for_set_id(latest_set_id)


@router.get("/{set_id}")
async def evaluation_set_detail(set_id: int) -> EvaluationSetDetail:
    """Returns detailed information about a specific evaluation set, including:
    - Submission statistics at each stage of the evaluation pipeline
    - Score statistics (best, average, and benchmark thresholds)
    - Comparison against the previous evaluation set's best score (if available)
    """

    set_id = await _resolve_set_id(set_id)

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

    def _rounded_float(value) -> float | None:
        return round(float(value), 4) if value is not None else None

    # 1. Validate that the evaluation set exists
    created_at = await get_set_created_at(set_id)
    if created_at is None:
        raise HTTPException(status_code=404, detail=f"Evaluation set {set_id} not found.")

    # 2. Fetch submission, score statistics, and competition info concurrently
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
        hardcoded_rejection_rate=(
            round(submission_row["failed_at_pre_screening_count"] / total, 4) if total > 0 else 0.0
        ),
        approved_emission_count=submission_row["approved_emission_count"],
        pipeline=pipeline,
    )

    scores = EvaluationSetDetailScores(
        best=(round(score_row["best"], 2) if score_row["best"] is not None else None),
        average=(round(score_row["average"], 2) if score_row["average"] is not None else None),
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
        lowest_average_cost_usd_top_agents=_rounded_float(
            leaderboard_summary_row["lowest_average_cost_usd_top_agents"]
        ),
        lowest_average_runtime_seconds_top_agents=_rounded_float(
            leaderboard_summary_row["lowest_average_runtime_seconds_top_agents"]
        ),
        average_agent_cost_usd=_rounded_float(leaderboard_summary_row["average_agent_cost_usd"]),
        average_agent_runtime_seconds=_rounded_float(leaderboard_summary_row["average_agent_runtime_seconds"]),
    )

    return EvaluationSetDetail(
        id=set_id,
        created_at=created_at,
        competition_name=competition_name,
        competition_start_date=competition_start_date,
        competition_end_date=competition_end_date,
        submissions=submissions,
        scores=scores,
        vs_previous_set=vs_previous_set,
        top_agent=top_agent,
        efficiency=efficiency,
    )


@router.get("/{set_id}/leaderboard")
async def evaluation_set_leaderboard(set_id: int) -> list[EvaluationSetDetailAgent]:
    set_id = await _resolve_set_id(set_id)

    if await get_set_created_at(set_id) is None:
        raise HTTPException(status_code=404, detail=f"Evaluation set {set_id} not found.")

    agent_rows = await get_evaluation_set_leaderboard_agents(set_id)
    return [EvaluationSetDetailAgent(**dict(row)) for row in agent_rows]
