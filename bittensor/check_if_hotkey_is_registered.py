import sys
import asyncio
from bittensor.core.async_subtensor import AsyncSubtensor

async def main():
    if len(sys.argv) != 4:
        print("Usage: python check_if_hotkey_is_registered.py <hotkey> <netuid> <subtensor_network>")
        sys.exit(1)

    hotkey = sys.argv[1]
    netuid = int(sys.argv[2])
    subtensor_network = sys.argv[3]

    subtensor = AsyncSubtensor(network=subtensor_network)

    is_registered = await subtensor.is_hotkey_registered(hotkey_ss58=hotkey, netuid=netuid)

    if is_registered:
        print(f"Hotkey {hotkey} is registered on subnet {netuid}")
        sys.exit(0)
    else:
        print(f"Hotkey {hotkey} is NOT registered on subnet {netuid}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
