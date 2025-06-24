import os

from fiber.chain.interface import get_substrate
from fiber.chain.fetch_nodes import get_nodes_for_netuid

async def get_subnet_hotkeys():
    substrate = get_substrate(
        subtensor_network=os.getenv("SUBTENSOR_NETWORK"), subtensor_address=os.getenv("SUBTENSOR_ADDRESS")
    )

    active_nodes = get_nodes_for_netuid(substrate, int(os.getenv("NETUID")))

    hotkeys = [node.hotkey for node in active_nodes]

    return hotkeys
