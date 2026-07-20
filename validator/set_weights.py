# NOTE ADAM: Subtensor bug (self.disable_third_party_loggers())
import logging
import math
from typing import Dict

from bittensor.core.async_subtensor import AsyncSubtensor
from bittensor.core.chain_data.metagraph_info import SelectiveMetagraphIndex

import validator.config as config

logger = logging.getLogger(__name__)


subtensor = AsyncSubtensor(network=config.SUBTENSOR_NETWORK, fallback_endpoints=[config.SUBTENSOR_ADDRESS])


async def _get_registered_uids() -> dict[str, int] | None:
    metagraph = await subtensor.get_metagraph_info(
        netuid=config.NETUID,
        selected_indices=[SelectiveMetagraphIndex.Hotkeys],
    )
    if metagraph is None or metagraph.hotkeys is None:
        logger.error("Could not retrieve subnet hotkeys; leaving on-chain weights unchanged")
        return None
    return {hotkey: uid for uid, hotkey in enumerate(metagraph.hotkeys)}


async def set_weights_from_mapping(weights_mapping: Dict[str, float]) -> None:
    if not isinstance(weights_mapping, dict) or not weights_mapping:
        raise ValueError("Expected a non-empty hotkey-to-weight mapping")

    requested: list[tuple[str, float]] = []
    for hotkey, weight in weights_mapping.items():
        if isinstance(weight, bool):
            raise ValueError(f"Weight for {hotkey} must be numeric")

        weight = float(weight)
        if not math.isfinite(weight) or weight <= 0:
            raise ValueError(f"Weight for {hotkey} must be finite and positive")

        requested.append((hotkey, weight))

    registered_uids = await _get_registered_uids()
    if registered_uids is None:
        return

    resolved_uids = [registered_uids.get(hotkey) for hotkey, _weight in requested]
    resolved = [
        (hotkey, uid, weight) for (hotkey, weight), uid in zip(requested, resolved_uids, strict=True) if uid is not None
    ]
    missing_hotkeys = [hotkey for (hotkey, _weight), uid in zip(requested, resolved_uids, strict=True) if uid is None]
    if missing_hotkeys:
        logger.warning(f"Skipping unregistered weight hotkeys: {', '.join(missing_hotkeys)}")

    if not resolved:
        owner_hotkey = await subtensor.get_subnet_owner_hotkey(netuid=config.NETUID)
        if owner_hotkey is None:
            logger.error("Could not resolve the subnet owner; leaving on-chain weights unchanged")
            return

        owner_uid = registered_uids.get(owner_hotkey)
        if owner_uid is None:
            logger.error("Subnet owner is not registered; leaving on-chain weights unchanged")
            return

        logger.warning(f"No requested hotkeys are registered; burning emissions to subnet owner {owner_hotkey}")
        resolved = [(owner_hotkey, owner_uid, 1.0)]

    total = sum(weight for _hotkey, _uid, weight in resolved)
    normalized_weights = [weight / total for _hotkey, _uid, weight in resolved]
    uids = [uid for _hotkey, uid, _weight in resolved]
    hotkeys = [hotkey for hotkey, _uid, _weight in resolved]

    logger.info(f"Setting weights for {len(resolved)} hotkey(s): {', '.join(hotkeys)}")

    success, message = await subtensor.set_weights(
        wallet=config.VALIDATOR_WALLET,
        netuid=config.NETUID,
        uids=uids,
        weights=normalized_weights,
        wait_for_inclusion=True,
        wait_for_finalization=True,
    )

    if success:
        logger.info(f"Set weights for {len(resolved)} hotkey(s)")
    else:
        logger.error(f"Failed to set weights: {message}")
