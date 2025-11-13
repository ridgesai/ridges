import utils.logger as logger

from bittensor_wallet.keypair import Keypair
from bittensor.core.async_subtensor import AsyncSubtensor
from api import config



async def check_if_hotkey_is_registered(hotkey: str) -> bool:
    subtensor = AsyncSubtensor(network=config.SUBTENSOR_NETWORK)
    metagraph = await subtensor.metagraph(netuid=config.NETUID)
    registered_hotkeys = {neuron.hotkey for neuron in metagraph.neurons}
    return hotkey in registered_hotkeys

def validate_signed_timestamp(timestamp: int, signed_timestamp: str, hotkey: str) -> bool:
    """
    Checks if a signed timestamp is validly signed by the provided hotkey.
    """

    try:
        keypair = Keypair(ss58_address=hotkey)
        return keypair.verify(str(timestamp), bytes.fromhex(signed_timestamp))
    except Exception as e:
        logger.warning(f"Error in validate_signed_timestamp(timestamp={timestamp}, signed_timestamp={signed_timestamp}, hotkey={hotkey}): {e}")
        return False