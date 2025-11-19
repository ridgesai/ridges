import api.config as config

from typing import Dict
from fastapi import APIRouter
from pydantic import BaseModel
from utils.bittensor import check_if_hotkey_is_registered
from queries.scores import get_weight_receiving_agent_hotkey



router = APIRouter()



# /scoring/weights
@router.get("/weights")
async def weights() -> Dict[str, float]:
    if config.BURN:
        # When burning, we assign 100% of emissions to the owner hotkey.
        return {config.OWNER_HOTKEY: 1.0}

    # Try to get the weight-receiving agent's hotkey (aka. the top agent's
    # hotkey). Make sure it is registered on the subnet. If so, assign 100% of
    # emissions to it.
    weight_receiving_hotkey = await get_weight_receiving_agent_hotkey()
    if weight_receiving_hotkey:
        if await check_if_hotkey_is_registered(weight_receiving_hotkey):
            return {weight_receiving_hotkey: 1.0}

    # If no weight-receiving agent is found, assign 100% of emissions to the
    # owner hotkey (to burn).
    return {config.OWNER_HOTKEY: 1.0}



# /scoring/thresholds
class ScoringThresholdsResponse(BaseModel):
    screener_1_threshold: float
    screener_2_threshold: float
    prune_threshold: float

@router.get("/thresholds")
async def thresholds() -> ScoringThresholdsResponse:
    return ScoringThresholdsResponse(
        screener_1_threshold=config.SCREENER_1_THRESHOLD,
        screener_2_threshold=config.SCREENER_2_THRESHOLD,
        prune_threshold=config.PRUNE_THRESHOLD
    )