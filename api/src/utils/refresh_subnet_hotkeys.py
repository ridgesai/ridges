from api import config

from bittensor.core.metagraph import Metagraph
from bittensor.core.subtensor import Subtensor

def check_if_hotkey_is_registered(hotkey: str) -> bool:
    subtensor = Subtensor(network=config.SUBTENSOR_NETWORK)
    metagraph = Metagraph(netuid=config.NETUID, network=config.SUBTENSOR_NETWORK, sync=True, subtensor=subtensor)
    registered_hotkeys = {neuron.hotkey for neuron in metagraph.neurons}
    return hotkey in registered_hotkeys
