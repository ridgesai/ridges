import asyncio
import utils.logger as logger
import json
import os
from typing import Set

from bittensor_wallet.keypair import Keypair
from bittensor.core.async_subtensor import AsyncSubtensor
from bittensor_wallet.keypair import Keypair

import api.config as config
import utils.logger as logger



subtensor = AsyncSubtensor(network=config.SUBTENSOR_NETWORK)

REGISTERED_HOTKEYS_FILE = os.path.join(os.path.dirname(__file__), 'registered_hotkeys.json')

async def fetch_and_save_registered_hotkeys() -> None:
    try:
        metagraph = await subtensor.metagraph(netuid=config.NETUID)
        registered_hotkeys = [neuron.hotkey for neuron in metagraph.neurons]
        
        with open(REGISTERED_HOTKEYS_FILE, 'w') as f:
            json.dump(registered_hotkeys, f, indent=2)
        
        logger.info(f"Successfully saved {len(registered_hotkeys)} registered hotkeys to {REGISTERED_HOTKEYS_FILE}")
    except Exception as e:
        logger.error(f"Error fetching and saving registered hotkeys: {e}")
        raise

def load_registered_hotkeys() -> Set[str]:
    try:
        if not os.path.exists(REGISTERED_HOTKEYS_FILE):
            logger.warning(f"Registered hotkeys file not found: {REGISTERED_HOTKEYS_FILE}")
            return set()
        
        with open(REGISTERED_HOTKEYS_FILE, 'r') as f:
            hotkeys_list = json.load(f)
        
        return set(hotkeys_list)
    except Exception as e:
        logger.error(f"Error loading registered hotkeys from {REGISTERED_HOTKEYS_FILE}: {e}")
        return set()

async def check_if_hotkey_is_registered(hotkey: str) -> bool:
    registered_hotkeys = load_registered_hotkeys()
    
    # If no hotkeys loaded from JSON, fall back to fetching from metagraph
    if not registered_hotkeys:
        logger.warning("No registered hotkeys found in JSON file, falling back to metagraph fetch")
        metagraph = await subtensor.metagraph(netuid=config.NETUID)
        registered_hotkeys = {neuron.hotkey for neuron in metagraph.neurons}
    
    return hotkey in registered_hotkeys

# async def check_if_hotkey_is_registered(hotkey: str) -> bool:
#     return await subtensor.is_hotkey_registered(hotkey_ss58=hotkey, netuid=config.NETUID)



def validate_signed_timestamp(timestamp: int, signed_timestamp: str, hotkey: str) -> bool:
    try:
        keypair = Keypair(ss58_address=hotkey)
        return keypair.verify(str(timestamp), bytes.fromhex(signed_timestamp))
    except Exception as e:
        logger.warning(f"Error in validate_signed_timestamp(timestamp={timestamp}, signed_timestamp={signed_timestamp}, hotkey={hotkey}): {e}")
        return False

if __name__ == "__main__":
    asyncio.run(fetch_and_save_registered_hotkeys())

