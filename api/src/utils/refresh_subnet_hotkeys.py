#!/usr/bin/env python3
"""
Background service to refresh subnet hotkeys cache.
This runs independently as a separate process to avoid memory leaks.
"""
import os
import json
import time
import logging
import gc
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
    substrate = None
    active_nodes = None
    hotkeys = None
    
    try:
        substrate = get_substrate(
            subtensor_network=os.getenv("SUBTENSOR_NETWORK"), 
            subtensor_address=os.getenv("SUBTENSOR_ADDRESS")
        )
        active_nodes = get_nodes_for_netuid(substrate, int(os.getenv("NETUID")))
        hotkeys = [node.hotkey for node in active_nodes]
        
        # Explicitly clean up node objects to prevent memory accumulation
        for node in active_nodes:
            # Clear any potential circular references in node objects
            if hasattr(node, '__dict__'):
                node.__dict__.clear()
        
        return hotkeys
        
    except Exception as e:
        logger.error(f"Error fetching hotkeys: {e}")
        return None
        
    finally:
        # Aggressive cleanup to prevent memory leaks
        try:
            # Close substrate connection
            if substrate is not None:
                if hasattr(substrate, 'close'):
                    substrate.close()
                # Clear substrate object references
                substrate = None
        except Exception as e:
            logger.warning(f"Error closing substrate connection: {e}")
        
        # Clear references to node objects
        if active_nodes is not None:
            active_nodes.clear()
            active_nodes = None
        
        # Clear hotkeys reference if it exists locally
        if 'hotkeys' in locals():
            del hotkeys
        
        # Force garbage collection to clean up any remaining references
        gc.collect()

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
    
    # Clear hotkeys reference and force cleanup
    if hotkeys is not None:
        hotkeys.clear()
        hotkeys = None
    
    # Force another garbage collection after cache update
    gc.collect()
    
def main():
    """Main loop that runs continuously updating the cache."""
    logger.info("Starting subnet hotkeys refresh service")
    
    # Log initial memory usage
    logger.info(f"Initial memory usage: {initial_memory:.1f}MB")
    
    iteration = 0
    while True:
        try:
            iteration += 1
            logger.debug(f"Starting iteration {iteration}")
            
            update_cache()
            
            logger.debug(f"Sleeping for {UPDATE_INTERVAL} seconds")
            time.sleep(UPDATE_INTERVAL)
            
        except KeyboardInterrupt:
            logger.info("Received interrupt signal, shutting down")
            break
        except Exception as e:
            logger.error(f"Unexpected error in main loop: {e}")
            # Force garbage collection on errors too
            gc.collect()
            time.sleep(UPDATE_INTERVAL)  # Wait before retrying

if __name__ == "__main__":
    main()