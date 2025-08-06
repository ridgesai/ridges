#!/usr/bin/env python3
import json
import time
from substrateinterface import SubstrateInterface
from typing import List

from loggers.logging_utils import get_logger

logger = get_logger(__name__)

def check_if_hotkey_is_registered(hotkey: str, pathname: str = "subnet_hotkeys_cache.json") -> bool:
    try:
        with open(pathname, 'r') as f:
            data = json.load(f)
            return hotkey in data["hotkeys"]
    except Exception as e:
        logger.error(f"Error checking if hotkey is registered: {e}")
        return False

def get_miner_hotkeys_on_subnet(netuid: int = 62, subtensor_url: str = "wss://entrypoint-finney.opentensor.ai:443") -> List[str]:
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

hotkeys = get_miner_hotkeys_on_subnet()

if hotkeys:
    with open("subnet_hotkeys_cache.json", 'w') as f:
        json.dump({"hotkeys": hotkeys, "timestamp": time.time()}, f)
    print(f"Updated cache with {len(hotkeys)} hotkeys")
else:
    logger.error("No hotkeys found on subnet")
