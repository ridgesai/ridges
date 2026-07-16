from datetime import datetime
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import api.config as config
from api.incentives import get_current_allocations
from models.evaluation_set import EvaluationSetGroup
from queries.evaluation_set import get_latest_set_id, get_set_created_at
from queries.statistics import (
    get_average_score_per_evaluation_set_group,
    get_average_wait_time_per_evaluation_set_group,
)
from utils.ttl import ttl_cache

router = APIRouter()


# /scoring/weights
@router.get("/weights")
async def weights() -> Dict[str, float]:
    allocations = await get_current_allocations()
    return allocations.hotkey_weights


# /scoring/screener-info
class ScoringScreenerInfoResponse(BaseModel):
    screener_1_threshold: float
    screener_2_threshold: float
    prune_threshold: float

    screener_1_average_score: Optional[float] = None
    screener_2_average_score: Optional[float] = None
    validator_average_score: Optional[float] = None

    screener_1_average_wait_time: Optional[float] = None
    screener_2_average_wait_time: Optional[float] = None
    validator_average_wait_time: Optional[float] = None


@router.get("/screener-info")
@ttl_cache(ttl_seconds=60)  # 1 minute
async def screener_info() -> ScoringScreenerInfoResponse:
    average_score_per_evaluation_set_group = await get_average_score_per_evaluation_set_group()
    average_wait_time_per_evaluation_set_group = await get_average_wait_time_per_evaluation_set_group()

    return ScoringScreenerInfoResponse(
        screener_1_threshold=config.SCREENER_1_THRESHOLD,
        screener_2_threshold=config.SCREENER_2_THRESHOLD,
        prune_threshold=config.PRUNE_THRESHOLD,
        screener_1_average_score=average_score_per_evaluation_set_group[EvaluationSetGroup.screener_1],
        screener_2_average_score=average_score_per_evaluation_set_group[EvaluationSetGroup.screener_2],
        validator_average_score=average_score_per_evaluation_set_group[EvaluationSetGroup.validator],
        screener_1_average_wait_time=average_wait_time_per_evaluation_set_group[EvaluationSetGroup.screener_1],
        screener_2_average_wait_time=average_wait_time_per_evaluation_set_group[EvaluationSetGroup.screener_2],
        validator_average_wait_time=average_wait_time_per_evaluation_set_group[EvaluationSetGroup.validator],
    )


# /scoring/latest-set-info
class ScoringLatestSetInfo(BaseModel):
    latest_set_id: int
    latest_set_created_at: datetime


@router.get("/latest-set-info")
async def latest_set_info() -> ScoringLatestSetInfo:
    latest_set_id = await get_latest_set_id()
    if latest_set_id is None:
        raise HTTPException(status_code=404, detail="No evaluation sets have been promoted yet.")
    latest_set_created_at = await get_set_created_at(latest_set_id)
    return ScoringLatestSetInfo(latest_set_id=latest_set_id, latest_set_created_at=latest_set_created_at)
