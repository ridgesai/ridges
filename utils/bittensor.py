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

async def set_weights_from_mapping(weights_mapping: dict, netuid: int, subtensor_network: str, subtensor_address: str, wallet_name: str, hotkey_name: str, timeout_seconds: int) -> None:
    if len(weights_mapping.keys()) != 1:
        logger.error("Expected one hotkey in weights mapping")
        return

    hotkey = list(weights_mapping.keys())[0]

    process = await asyncio.create_subprocess_exec(
        "uv", "run", "bittensor/set_weights.py",
        hotkey,
        str(netuid),
        subtensor_network,
        subtensor_address,
        wallet_name,
        hotkey_name,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_seconds
        )

        if stdout:
            logger.info(stdout.decode().strip())
        if stderr:
            logger.error(stderr.decode().strip())
    except asyncio.TimeoutError:
        logger.error(f"Timeout setting weights after {timeout_seconds} seconds")
        try:
            process.kill()
            await process.wait()
        except:
            pass
        raise

def validate_signed_timestamp(timestamp: int, signed_timestamp: str, hotkey: str) -> bool:
    try:
        keypair = Keypair(ss58_address=hotkey)
        return keypair.verify(str(timestamp), bytes.fromhex(signed_timestamp))
    except Exception as e:
        logger.warning(f"Error in validate_signed_timestamp(timestamp={timestamp}, signed_timestamp={signed_timestamp}, hotkey={hotkey}): {e}")
        return False