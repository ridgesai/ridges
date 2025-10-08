import json
import os
import time
import threading
import asyncio
from typing import Optional, List, Any
from substrateinterface import SubstrateInterface
from pathlib import Path
from loggers.logging_utils import get_logger

logger = get_logger(__name__)

NETUID = os.getenv("NETUID", "62")
SUBTENSOR_URL = os.getenv("SUBTENSOR_ADDRESS", "ws://127.0.0.1:9945")
CACHE_FILE = Path("subnet_hotkeys_cache.json")

_subscription_thread: Optional[threading.Thread] = None
_polling_task: Optional[asyncio.Task] = None

def _update_cache() -> None:
    try:
        substrate = SubstrateInterface(url=SUBTENSOR_URL, ss58_format=42, type_registry_preset="substrate-node-template")
        result = substrate.query_map("SubtensorModule", "Uids", [NETUID])
        
        hotkeys = []
        for uid_data in result:
            try:
                hotkey = uid_data[0]
                if hasattr(hotkey, 'value'):
                    hotkey = hotkey.value
                if isinstance(hotkey, bytes):
                    hotkey = substrate.ss58_encode(hotkey)
                hotkeys.append(hotkey)
            except:
                continue
        
        temp_file = CACHE_FILE.with_suffix('.tmp')
        with open(temp_file, 'w') as f:
            json.dump({"hotkeys": hotkeys, "timestamp": time.time(), "count": len(hotkeys)}, f)
        temp_file.replace(CACHE_FILE)
        
        logger.info(f"Updated cache with {len(hotkeys)} hotkeys")
        substrate.close()
    except Exception as e:
        logger.error(f"Failed to update cache: {e}")

def _run_subscription_thread() -> None:
    while _subscription_thread and _subscription_thread.is_alive():
        try:
            substrate = SubstrateInterface(url=SUBTENSOR_URL, ss58_format=42, type_registry_preset="substrate-node-template")
            # Use query with subscription_handler to monitor SubnetworkN
            # This storage item tracks the number of UIDs and changes when miners register/deregister
            # https://docs.learnbittensor.org/subtensor-nodes/subtensor-storage-query-examples?#114-subnetworkn
            substrate.query(
                module="SubtensorModule",
                storage_function="SubnetworkN", 
                params=[NETUID],
                subscription_handler=lambda obj, update_nr, _: (
                    logger.info(f"SubnetworkN changed (update #{update_nr}), refreshing hotkey cache"),
                    _update_cache()
                )[1]
            )
        except Exception as e:
            # Filter out expected connection errors like:
            # "Expecting value: line 1 column 1 (char 0)", "Connection closed", "WebSocket connection is closed"
            if not any(x in str(e).lower() for x in ["expecting value", "json", "connection", "closed"]):
                logger.error(f"Subscription error: {e}")
            time.sleep(5)
        finally:
            try:
                substrate.close()
            except:
                pass

async def _polling_loop() -> None:
    """Poll for hotkey updates every 15 minutes as a backup to subscription."""
    while _polling_task and not _polling_task.cancelled():
        try:
            await asyncio.sleep(15 * 60)  # 15 minutes
            if _polling_task and not _polling_task.cancelled():
                logger.info("Periodic hotkey cache refresh (15min polling)")
                _update_cache()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Polling error: {e}")

async def start_hotkey_subscription() -> None:
    global _subscription_thread, _polling_task
    if _subscription_thread and _subscription_thread.is_alive():
        return
    logger.info("Starting hotkey subscription service with 15min polling backup")
    _update_cache()
    _subscription_thread = threading.Thread(target=_run_subscription_thread, daemon=True)
    _subscription_thread.start()
    _polling_task = asyncio.create_task(_polling_loop())

async def stop_hotkey_subscription() -> None:
    global _subscription_thread, _polling_task
    if _subscription_thread:
        logger.info("Stopped hotkey subscription service")
        _subscription_thread = None
    if _polling_task:
        _polling_task.cancel()
        _polling_task = None
