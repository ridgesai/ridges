import os
from typing import List

from fiber.chain.interface import get_substrate
from fiber.chain.fetch_nodes import get_nodes_for_netuid

from loggers.logging_utils import get_logger

logger = get_logger(__name__)


def get_registered_validator_hotkeys() -> List[str]:
    """Get list of all registered validator hotkeys from the metagraph."""
    try:
        substrate = get_substrate(
            subtensor_network=os.getenv("SUBTENSOR_NETWORK", "test"),
            subtensor_address=os.getenv("SUBTENSOR_ADDRESS", "ws://127.0.0.1:9945"),
        )
        netuid = int(os.getenv("NETUID", "1"))

        # Get all nodes for the netuid
        nodes = get_nodes_for_netuid(substrate, netuid)

        # Extract hotkeys from all nodes (both miners and validators)
        hotkeys = [node.hotkey for node in nodes]

        logger.info(f"Found {len(hotkeys)} registered nodes on subnet {netuid}")
        return hotkeys

    except Exception as e:
        logger.error(f"Failed to get registered validator hotkeys: {e}")
        return []


def is_validator_registered(validator_hotkey: str) -> bool:
    """Check if a validator hotkey is registered in the metagraph."""
    try:
        registered_hotkeys = get_registered_validator_hotkeys()
        is_registered = validator_hotkey in registered_hotkeys

        if is_registered:
            logger.info(f"Validator hotkey {validator_hotkey} is registered in the metagraph")
        else:
            logger.warning(f"Validator hotkey {validator_hotkey} is NOT registered in the metagraph")

        return is_registered

    except Exception as e:
        logger.error(f"Error checking validator registration for {validator_hotkey}: {e}")
        return False
