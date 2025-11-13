from api import config

from bittensor.core.async_subtensor import AsyncSubtensor

async def check_if_hotkey_is_registered(hotkey: str) -> bool:
    subtensor = AsyncSubtensor(network=config.SUBTENSOR_NETWORK)
    metagraph = await subtensor.metagraph(netuid=config.NETUID, lite=True)
    registered_hotkeys = {neuron.hotkey for neuron in metagraph.neurons}
    return hotkey in registered_hotkeys
