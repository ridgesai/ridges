#!/usr/bin/env python3
import json
import time
from substrateinterface import SubstrateInterface
from typing import List, Set

import utils.logger as logger
from api import config

# Global in-memory cache for hotkeys
_hotkeys_cache: Set[str] = set()
_cache_timestamp: float = 0.0


def check_if_hotkey_is_registered(hotkey: str, pathname: str = "subnet_hotkeys_cache.json") -> bool:
    """Check if a hotkey is registered in the subnet using in-memory cache.

    Args:
        hotkey: The hotkey to check
        pathname: Kept for backward compatibility, but not used

    Returns:
        bool: True if hotkey is registered, False otherwise
    """
    try:
        return hotkey in _hotkeys_cache
    except Exception as e:
        logger.error(f"Error checking if hotkey is registered: {e}")
        return False


def refresh_hotkeys_cache() -> bool:
    """Refresh the in-memory hotkeys cache by fetching from the subnet.

    Returns:
        bool: True if refresh was successful, False otherwise
    """
    global _hotkeys_cache, _cache_timestamp

    try:
        hotkeys = get_miner_hotkeys_on_subnet(
            netuid=config.NETUID,
            subtensor_url=config.SUBTENSOR_ADDRESS
        )

        if hotkeys:
            _hotkeys_cache = set(hotkeys)
            _cache_timestamp = time.time()
            logger.info(f"Refreshed hotkeys cache with {len(hotkeys)} hotkeys")
            return True
        else:
            logger.error("No hotkeys found on subnet during cache refresh")
            return False

    except Exception as e:
        logger.error(f"Error refreshing hotkeys cache: {e}")
        return False


def get_cached_hotkeys() -> Set[str]:
    """Get the current set of cached hotkeys.

    Returns:
        Set[str]: Set of cached hotkeys
    """
    return _hotkeys_cache.copy()


def get_cache_timestamp() -> float:
    """Get the timestamp of the last cache refresh.

    Returns:
        float: Unix timestamp of last refresh
    """
    return _cache_timestamp

def get_miner_hotkeys_on_subnet(netuid: int, subtensor_url: str) -> List[str]:
    substrate = None

    try:
        substrate = SubstrateInterface(
            url=subtensor_url,
            ss58_format=42,
            type_registry_preset="substrate-node-template"
        )
        
        result = substrate.query_map(
            module="SubtensorModule",
            storage_function="Uids",
            params=[netuid]
        )
        
        miner_hotkeys = []
        
        for uid_data in result:
            try:
                hotkey = uid_data[0]
                uid = uid_data[1].value
                
                if hotkey:
                    if hasattr(hotkey, 'value'):
                        hotkey = hotkey.value
                    
                    if isinstance(hotkey, bytes):
                        hotkey = substrate.ss58_encode(hotkey)
                    miner_hotkeys.append(hotkey)
            except Exception as e:
                logger.warning(f"Error processing UID entry: {e}")
                continue
        
        logger.info(f"Found {len(miner_hotkeys)} miner hotkeys on subnet {netuid}")
        return miner_hotkeys
        
    except Exception as e:
        logger.error(f"Error getting miner hotkeys from subnet {netuid}: {e}")
        return []
    finally:
        if substrate is not None:
            substrate.close()







# Initialize the cache when the module is loaded
if refresh_hotkeys_cache():
    print(f"Initialized hotkeys cache with {len(_hotkeys_cache)} hotkeys")
else:
    logger.error("Failed to initialize hotkeys cache")
