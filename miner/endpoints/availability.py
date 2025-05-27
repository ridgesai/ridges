from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from logging_utils import get_logger

from miner.utils.shared import miner_lock

logger = get_logger(__name__)

class AvailabilityResponse(BaseModel):
    available: bool

# Create router instance
router = APIRouter()

# This lets validators ping the miner and see if they are available and running to send queries to
@router.get("/availability", response_model=AvailabilityResponse)
async def check_availability():
    """Check if the miner is available to process a challenge."""
    is_available = not miner_lock.locked()
    logger.info(f"Miner availability checked: {is_available}")
    return AvailabilityResponse(available=is_available)