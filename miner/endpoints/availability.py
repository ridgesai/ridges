from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from shared.logging_utils import get_logger

from miner.utils.shared import worker_manager

logger = get_logger(__name__)

class AvailabilityResponse(BaseModel):
    available: bool
    queue_size: int = 0
    active_challenges: int = 0
    max_queue_size: int = 0

# Create router instance
router = APIRouter()

# This lets validators ping the miner and see if they are available and running to send queries to
@router.get("/availability", response_model=AvailabilityResponse)
async def check_availability():
    """Check if the miner is available to process a challenge."""
    is_available = not worker_manager.challenge_queue.is_full
    queue_size = worker_manager.challenge_queue.size
    active_count = worker_manager.challenge_queue.active_count
    max_size = worker_manager.challenge_queue.queue.maxsize
    
    logger.info(f"Miner availability checked: {is_available} (queue: {queue_size}, active: {active_count}, max: {max_size})")
    
    return AvailabilityResponse(
        available=is_available,
        queue_size=queue_size,
        active_challenges=active_count,
        max_queue_size=max_size
    )
