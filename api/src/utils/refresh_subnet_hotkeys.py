from api import config

from bittensor.core.metagraph import Metagraph
from bittensor.core.subtensor import Subtensor

network = config.SUBTENSOR_NETWORK
address = config.SUBTENSOR_ADDRESS
netuid = config.NETUID

def check_if_hotkey_is_registered(hotkey: str) -> bool:
    subtensor = Subtensor(network=network)
    metagraph = Metagraph(netuid=netuid, network=network, sync=True, subtensor=subtensor)
    registered_hotkeys = {neuron.hotkey for neuron in metagraph.neurons if not neuron.validator_permit}
    return hotkey in registered_hotkeys
