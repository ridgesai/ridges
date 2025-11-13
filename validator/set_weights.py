from typing import Dict
from bittensor.core.async_subtensor import AsyncSubtensor
from bittensor_wallet import Wallet
import utils.logger as logger
from validator import config



async def set_weights_from_mapping(weights_mapping: Dict[str, float]) -> None:
    if len(weights_mapping.keys()) < 1:
        logger.warning("Expected at least one top miner, but got 0")
        return

    subtensor = AsyncSubtensor(network=config.SUBTENSOR_NETWORK, fallback_endpoints=[config.SUBTENSOR_ADDRESS])
    top_miner_hotkey = list(weights_mapping.keys())[0] # Currently we just use one top miner
    validator_wallet = Wallet(name=config.VALIDATOR_WALLET_NAME, hotkey=config.VALIDATOR_HOTKEY_NAME)
    top_miner_uid = await subtensor.get_uid_for_hotkey_on_subnet(hotkey_ss58=top_miner_hotkey, netuid=config.NETUID)

    logger.info(f"Setting weights for top miner: {top_miner_hotkey} with weight {weights_mapping[top_miner_hotkey]}")

    success, message = await subtensor.set_weights(
        wallet=validator_wallet,
        netuid=config.NETUID,
        uids=[top_miner_uid],
        weights=[weights_mapping[top_miner_hotkey]],
        wait_for_inclusion=True,
        wait_for_finalization=True
    )
    logger.info(f"Success: {success}, Message: {message}")
