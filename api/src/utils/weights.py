import sys
import os
import time
from datetime import datetime
from bittensor.core.subtensor import Subtensor

# Add the project root directory to the Python path (FIX THIS)
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

from api.src.db.operations import DatabaseManager
from api.src.utils.logging_utils import get_logger

logger = get_logger(__name__)

db = DatabaseManager()

def get_miner_weights(netuid=62):
    """Get a dictionary mapping miner UIDs to their weights as decimals."""
    
    bittensor_client = Subtensor(network="finney")

    try:
        weights_data = bittensor_client.weights(netuid=netuid)
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

def run_weight_monitor(netuid=62, interval_seconds=12):
    """Continuously monitor miner weights, updating every specified interval."""
    
    logger.info(f"Starting weight monitor for subnet {netuid}. Updating every {interval_seconds} seconds")
    
    try:
        while True:
            weights = get_miner_weights(netuid=netuid)
            latest_stored = db.get_latest_weights()
            
            if latest_stored:
                stored_weights = latest_stored['weights']
                stored_timestamp = latest_stored['timestamp']
                
                time_since_last = datetime.now() - stored_timestamp if stored_timestamp else None
                
                weights_changed = db.weights_are_different(weights, stored_weights)
                
                if weights_changed:
                    logger.info(f"Weights have been updated. Storing new weights. Time since last update: {time_since_last}")
                    
                    db.store_weights(weights, time_since_last)
                else:
                    logger.info(f"Weights unchanged, skipping storage. Last update: {stored_timestamp}. Time since last update: {time_since_last}")
            else:
                logger.info(f"No previous weights found, storing initial weights...")
                db.store_weights(weights)
            
            logger.info(f"Next weight check in {interval_seconds} seconds...")
            time.sleep(interval_seconds)
            
    except Exception as e:
        logger.error(f"Error in weight monitor: {e}")

if __name__ == "__main__":
    run_weight_monitor(netuid=62) 
