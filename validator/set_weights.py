from bittensor.core.async_subtensor import AsyncSubtensor
from utils.bittensor import check_if_hotkey_is_registered, is_valid_hotkey_string
import utils.logger as logger
import validator.config as config

from typing import Dict

subtensor = AsyncSubtensor(network=config.SUBTENSOR_NETWORK, fallback_endpoints=[config.SUBTENSOR_ADDRESS])

async def set_weights_from_mapping(weights_mapping: Dict[str, float]) -> None:
    if len(weights_mapping.keys()) != 1:
        logger.error("Expected one hotkey")
        return

    weight_receiving_hotkey = list(weights_mapping.keys())[0]
    if weights_mapping[weight_receiving_hotkey] != 1:
        logger.error("Expected weight of 1")
        return

    if not is_valid_hotkey_string(weight_receiving_hotkey):
        logger.error(f"Weight receiving hotkey {weight_receiving_hotkey} is not a valid Base58 encoded string")
        return

    if not await check_if_hotkey_is_registered(weight_receiving_hotkey):
        logger.error(f"Weight receiving hotkey {weight_receiving_hotkey} not registered")
        return

    weight_receiving_uid = await subtensor.get_uid_for_hotkey_on_subnet(hotkey_ss58=weight_receiving_hotkey, netuid=config.NETUID)
    if weight_receiving_uid is None:
        logger.error(f"Weight receiving hotkey {weight_receiving_hotkey} not found")
        return

    logger.info(f"Setting weight of {weight_receiving_hotkey} to 1...")

    success, message = await subtensor.set_weights(
        wallet=config.VALIDATOR_WALLET,
        netuid=config.NETUID,
        uids=[weight_receiving_uid],
        weights=[1],
        wait_for_inclusion=True,
        wait_for_finalization=True
    )

    if success:
        logger.info(f"Set weight of hotkey {weight_receiving_hotkey} to 1")
    else:
        logger.error(f"Failed to set weight of hotkey {weight_receiving_hotkey} to 1: {message}")
