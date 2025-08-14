import json
import logging
from datetime import datetime, timezone
from typing import Optional, List

import asyncpg

from api.src.backend.db_manager import db_operation, db_transaction
from api.src.utils.models import WeightsData

logger = logging.getLogger(__name__)

@db_transaction
async def store_weights(conn: asyncpg.Connection, miner_weights: dict, time_since_last_update=None) -> int:
    """
    Store miner weights - deprecated function, now returns success without storing.
    """
    logger.info(f"Weights storage deprecated - skipping storage of {len(miner_weights)} miners")
    return 1

@db_operation
async def get_latest_weights(conn: asyncpg.Connection) -> Optional[dict]:
    """
    Get the most recent weights - deprecated function, now returns None.
    """
    logger.info("Latest weights deprecated - returning None")
    return None

def weights_are_different(current_weights: dict, stored_weights: dict) -> bool:
    """
    Compare current weights with stored weights to check if they're different.
    Returns True if weights are different, False if they're the same.
    """
    try:
        current_uids = set(str(uid) for uid in current_weights.keys())
        stored_uids = set(str(uid) for uid in stored_weights.keys())
        
        if current_uids != stored_uids:
            added_miners = current_uids - stored_uids
            removed_miners = stored_uids - current_uids
            
            if added_miners:
                logger.info(f"Added miners: {added_miners}. Updating weights in database.")
            if removed_miners:
                logger.info(f"Removed miners: {removed_miners}. Updating weights in database.")
            
            return True
        
        # Check if any weights have changed (with small tolerance for floating point)
        tolerance = 1e-6
        for uid in current_weights.keys():
            current_weight = current_weights[uid]
            stored_weight = stored_weights.get(str(uid))
            
            if stored_weight is None:
                logger.info(f"UID {uid} not found in stored weights. Updating weights in database.")
                return True
            
            if abs(current_weight - stored_weight) > tolerance:
                logger.info(f"Weight changed for UID {uid}: {stored_weight} -> {current_weight}. Updating database.")
                return True
        
        return False
    except Exception as e:
        logger.error(f"Error comparing weights: {str(e)}")
        return False

@db_operation
async def get_weights_history_last_24h_with_prior(conn: asyncpg.Connection) -> List[WeightsData]:
    """
    Returns weights history - deprecated function, now returns empty list.
    """
    logger.info("Weights history deprecated - returning empty list")
    return []

