import sys
import asyncio
import os
from bittensor.core.async_subtensor import AsyncSubtensor

# set project root to sys.path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import config

async def main():
    if len(sys.argv) != 2:
        print("Usage: python set_weights_cli.py <hotkey>")
        sys.exit(1)

    hotkey = sys.argv[1]

    subtensor = AsyncSubtensor(network=config.SUBTENSOR_NETWORK, fallback_endpoints=[config.SUBTENSOR_ADDRESS])

    uid = await subtensor.get_uid_for_hotkey_on_subnet(hotkey_ss58=hotkey, netuid=config.NETUID)
    if uid is None:
        print(f"Hotkey {hotkey} not found on subnet {config.NETUID}")
        sys.exit(1)

    print(f"Setting weight for hotkey {hotkey} (UID {uid}) to 1...")

    success, message = await subtensor.set_weights(
        wallet=config.VALIDATOR_WALLET,
        netuid=config.NETUID,
        uids=[uid],
        weights=[1],
        wait_for_inclusion=True,
        wait_for_finalization=True
    )

    if success:
        print(f"Successfully set weight for hotkey {hotkey} to 1")
    else:
        print(f"Failed to set weight: {message}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
