from bittensor.core.async_subtensor import AsyncSubtensor
import api.config as config
import utils.logger as logger
import base58
from bittensor_wallet.keypair import Keypair



VALID_HOTKEY_LENGTH = 48
subtensor = AsyncSubtensor(network=config.SUBTENSOR_NETWORK)



async def check_if_hotkey_is_registered(hotkey: str) -> bool:
    return await subtensor.is_hotkey_registered(hotkey_ss58=hotkey, netuid=config.NETUID)



def validate_signed_timestamp(timestamp: int, signed_timestamp: str, hotkey: str) -> bool:
    try:
        keypair = Keypair(ss58_address=hotkey)
        return keypair.verify(str(timestamp), bytes.fromhex(signed_timestamp))
    except Exception as e:
        logger.warning(f"Error in validate_signed_timestamp(timestamp={timestamp}, signed_timestamp={signed_timestamp}, hotkey={hotkey}): {e}")
        return False



def is_valid_hotkey_string(hotkey: str) -> bool:
    try:
        base58.b58decode(hotkey)
        return len(hotkey) == VALID_HOTKEY_LENGTH
    except ValueError:
        return False
