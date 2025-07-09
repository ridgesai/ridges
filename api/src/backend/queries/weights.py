import json
import logging
from datetime import datetime, timezone
from typing import Optional, List

import asyncpg

from api.src.backend.db_manager import db_operation
from api.src.utils.models import WeightsData

logger = logging.getLogger(__name__)

@db_operation
async def store_weights(conn: asyncpg.Connection, miner_weights: dict, time_since_last_update=None) -> int:
    """
    Store miner weights in the weights_history table. Return 1 if successful, 0 if not.
    """
    try:
        await conn.execute("""
            INSERT INTO weights_history (timestamp, time_since_last_update, miner_weights)
            VALUES ($1, $2, $3)
        """, datetime.now(timezone.utc), time_since_last_update, json.dumps(miner_weights))
        
        logger.info(f"Weights stored successfully with {len(miner_weights)} miners")
        return 1
    except Exception as e:
        logger.error(f"Error storing weights: {str(e)}")
        return 0

@db_operation
async def get_latest_weights(conn: asyncpg.Connection) -> Optional[dict]:
    """
    Get the most recent weights from the weights_history table. Return None if not found.
    """
    try:
        row = await conn.fetchrow("""
            SELECT miner_weights, timestamp, time_since_last_update
            FROM weights_history 
            ORDER BY timestamp DESC 
            LIMIT 1
        """)
        
        if row:
            return {
                'weights': json.loads(row['miner_weights']),
                'timestamp': row['timestamp'],
                'time_since_last_update': row['time_since_last_update']
            }
        return None
    except Exception as e:
        logger.error(f"Error getting latest weights: {str(e)}")
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
    Returns all rows from weights_history with timestamp >= NOW() - INTERVAL '24 hours',
    plus the single row that immediately precedes that window (for continuity).
    Returns a list of WeightsData models.
    """
    try:
        rows = await conn.fetch("""
            (
                SELECT id, timestamp, time_since_last_update, miner_weights
                FROM weights_history
                WHERE timestamp < NOW() - INTERVAL '24 hours'
                ORDER BY timestamp DESC
                LIMIT 1
            )
            UNION ALL
            (
                SELECT id, timestamp, time_since_last_update, miner_weights
                FROM weights_history
                WHERE timestamp >= NOW() - INTERVAL '24 hours'
                ORDER BY timestamp ASC
            )
            ORDER BY timestamp ASC
        """)
        
        return [WeightsData(
            id=str(row['id']),
            timestamp=row['timestamp'],
            time_since_last_update=row['time_since_last_update'],
            miner_weights=json.loads(row['miner_weights'])
        ) for row in rows]
    except Exception as e:
        logger.error(f"Error fetching weights_history for last 24h with prior: {str(e)}")
        return []

