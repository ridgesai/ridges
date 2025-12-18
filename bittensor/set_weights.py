import sys
import asyncio
from bittensor.core.async_subtensor import AsyncSubtensor
from bittensor_wallet.wallet import Wallet

async def main():
    if len(sys.argv) != 7:
        print("Usage: python set_weights.py <hotkey> <netuid> <subtensor_network> <subtensor_address> <wallet_name> <hotkey_name>")
        sys.exit(1)

    target_hotkey = sys.argv[1]
    netuid = int(sys.argv[2])
    subtensor_network = sys.argv[3]
    subtensor_address = sys.argv[4]
    wallet_name = sys.argv[5]
    hotkey_name = sys.argv[6]

    wallet = Wallet(name=wallet_name, hotkey=hotkey_name)
    subtensor = AsyncSubtensor(network=subtensor_network, fallback_endpoints=[subtensor_address])

    uid = await subtensor.get_uid_for_hotkey_on_subnet(hotkey_ss58=target_hotkey, netuid=netuid)
    if uid is None:
        print(f"Hotkey {target_hotkey} not found on subnet {netuid}")
        sys.exit(1)

    print(f"Setting weight for hotkey {target_hotkey} (UID {uid}) to 1...")

    success, message = await subtensor.set_weights(
        wallet=wallet,
        netuid=netuid,
        uids=[uid],
        weights=[1],
        wait_for_inclusion=True,
        wait_for_finalization=True
    )

    if success:
        print(f"Successfully set weight for hotkey {target_hotkey} to 1")
    else:
        print(f"Failed to set weight: {message}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
