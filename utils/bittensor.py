# NOTE ADAM: Subtensor bug (self.disable_third_party_loggers())
import asyncio
from bittensor_wallet.keypair import Keypair

import api.config as config
import utils.logger as logger



async def check_if_hotkey_is_registered(hotkey: str) -> bool:
    process = await asyncio.create_subprocess_exec(
        "uv", "run", "bittensor/check_if_hotkey_is_registered.py",
        hotkey,
        str(config.NETUID),
        config.SUBTENSOR_NETWORK,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL
    )
    await process.wait()
    return process.returncode == 0

def validate_signed_timestamp(timestamp: int, signed_timestamp: str, hotkey: str) -> bool:
    try:
        keypair = Keypair(ss58_address=hotkey)
        return keypair.verify(str(timestamp), bytes.fromhex(signed_timestamp))
    except Exception as e:
        logger.warning(f"Error in validate_signed_timestamp(timestamp={timestamp}, signed_timestamp={signed_timestamp}, hotkey={hotkey}): {e}")
        return False