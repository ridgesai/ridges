import asyncio
from datetime import datetime

from api.src.db.operations import DatabaseManager
from api.src.utils.logging_utils import get_logger
from api.src.utils.subtensor import get_current_weights

logger = get_logger(__name__)

db = DatabaseManager()

def get_miner_weights(netuid=62):
    """Get a dictionary mapping miner UIDs to their weights as decimals."""
    
    try:
        weights_data = get_current_weights(netuid=netuid)
    except Exception as e:
        logger.error(f"Error getting weights: {e}")
        return {}
    
    miner_weights = {}
    for validator_id, target_weights in weights_data:
        for target_id, weight in target_weights:
            if target_id not in miner_weights:
                miner_weights[target_id] = 0
            miner_weights[target_id] += weight
    
    total_weight = sum(miner_weights.values())
    if total_weight > 0:
        miner_weights_decimal = {uid: weight / total_weight for uid, weight in miner_weights.items()}
    else:
        miner_weights_decimal = {}
    
    return miner_weights_decimal

async def run_weight_monitor(netuid=62, interval_seconds=60):
    """Continuously monitor miner weights, updating every specified interval."""
    
    logger.info(f"Starting weight monitor for subnet {netuid}. Updating every {interval_seconds} seconds")
    
    while True:
        try:
            # Check connection pool status before proceeding
            pool_status = await db.get_pool_status()
            if "error" not in pool_status and pool_status["checked_out"] > (pool_status["pool_size"] * 0.9):
                logger.warning(f"Connection pool nearly exhausted ({pool_status['checked_out']}/{pool_status['pool_size']}), skipping weight check")
                await asyncio.sleep(interval_seconds)
                continue
            
            weights = get_miner_weights(netuid=netuid)
            latest_stored = await db.get_latest_weights()
            
            if latest_stored:
                stored_weights = latest_stored['weights']
                stored_timestamp = latest_stored['timestamp']
                
                time_since_last = datetime.now() - stored_timestamp if stored_timestamp else None
                
                weights_changed = db.weights_are_different(weights, stored_weights)
                
                if weights_changed:
                    logger.info(f"Weights have been updated. Storing new weights. Time since last update: {time_since_last}")
                    
                    await db.store_weights(weights, time_since_last)
                else:
                    logger.info(f"Weights unchanged, skipping storage. Last update: {stored_timestamp}. Time since last update: {time_since_last}")
            else:
                logger.info(f"No previous weights found, storing initial weights...")
                await db.store_weights(weights)
            
            logger.info(f"Next weight check in {interval_seconds} seconds...")
            await asyncio.sleep(interval_seconds)
        except Exception as e:
            logger.error(f"Error in weight monitor: {e}")
            await asyncio.sleep(interval_seconds)
        
