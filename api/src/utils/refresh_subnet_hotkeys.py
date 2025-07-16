#!/usr/bin/env python3
"""
Background service to refresh subnet hotkeys cache.
This runs independently as a separate process to avoid memory leaks.
"""
import os
import json
import time
import logging
from pathlib import Path
import dotenv

from fiber.chain.interface import get_substrate
from fiber.chain.fetch_nodes import get_nodes_for_netuid

dotenv.load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Cache file path
CACHE_FILE = Path("subnet_hotkeys_cache.json")
UPDATE_INTERVAL = 60  # seconds

def fetch_hotkeys():
    """Fetch hotkeys from the subnet and return as list."""
    try:
        substrate = None
        try:
            substrate = get_substrate(
                subtensor_network=os.getenv("SUBTENSOR_NETWORK"), 
                subtensor_address=os.getenv("SUBTENSOR_ADDRESS")
            )
            active_nodes = get_nodes_for_netuid(substrate, int(os.getenv("NETUID")))
            hotkeys = [node.hotkey for node in active_nodes]
            return hotkeys
        finally:
            # Close substrate connection if it exists
            if substrate is not None and hasattr(substrate, 'close'):
                substrate.close()
                
    except Exception as e:
        logger.error(f"Error fetching hotkeys: {e}")
        return None

def update_cache():
    """Fetch hotkeys and write to cache file."""
    hotkeys = fetch_hotkeys()
    if hotkeys is not None:
        try:
            with open(CACHE_FILE, 'w') as f:
                json.dump({"hotkeys": hotkeys, "timestamp": time.time()}, f)
            logger.info(f"Updated hotkeys cache with {len(hotkeys)} hotkeys")
        except Exception as e:
            logger.error(f"Error writing to cache file: {e}")
    else:
        logger.warning("Failed to fetch hotkeys, cache not updated")

def main():
    """Main loop that runs continuously updating the cache."""
    logger.info("Starting subnet hotkeys refresh service")
    
    while True:
        try:
            update_cache()
            logger.debug(f"Sleeping for {UPDATE_INTERVAL} seconds")
            time.sleep(UPDATE_INTERVAL)
        except KeyboardInterrupt:
            logger.info("Received interrupt signal, shutting down")
            break
        except Exception as e:
            logger.error(f"Unexpected error in main loop: {e}")
            time.sleep(UPDATE_INTERVAL)  # Wait before retrying

if __name__ == "__main__":
    main()