from substrateinterface import SubstrateInterface
import dotenv
import json
from pathlib import Path
import aiofiles

from loggers.logging_utils import get_logger

dotenv.load_dotenv()

logger = get_logger(__name__)

# Hotkeys cache
_hotkeys_cache_file = Path("subnet_hotkeys_cache.json")

async def get_subnet_hotkeys():
    """Get subnet hotkeys from cache file."""
    try:
        if _hotkeys_cache_file.exists():
            async with aiofiles.open(_hotkeys_cache_file, 'r') as f:
                content = await f.read()
                data = json.loads(content)
                return data["hotkeys"]
        else:
            logger.warning("Hotkeys cache file does not exist. Make sure hotkey subscription service is running.")
            return []
    except Exception as e:
        logger.error(f"Error reading hotkeys cache: {e}")
        return []

async def check_if_hotkey_is_registered(hotkey: str) -> bool:
    """Check if a hotkey is registered on the subnet by looking in the cache."""
    try:
        hotkeys = await get_subnet_hotkeys()
        return hotkey in hotkeys
    except Exception as e:
        logger.error(f"Error checking if hotkey is registered: {e}")
        return False
