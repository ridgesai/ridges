from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel

from models.evaluation_set import EvaluationSetGroup
from queries.agent import get_queue_depth, get_active_evaluation_count

router = APIRouter()


class QueueDepthResponse(BaseModel):
    depth: int
    stage: str
    active: int


@router.get("/queue-depth", response_model=QueueDepthResponse)
async def queue_depth(
    stage: str = Query(..., pattern="^(screener_1|screener_2|validator)$"),
):
    group = EvaluationSetGroup(stage)
    depth = await get_queue_depth(group)
    active = await get_active_evaluation_count(group)
    return QueueDepthResponse(depth=depth, stage=stage, active=active)
