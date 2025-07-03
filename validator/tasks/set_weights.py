import asyncio
from typing import List

from fiber import SubstrateInterface
from fiber.chain import chain_utils, interface, metagraph, weights
from fiber.chain.fetch_nodes import get_nodes_for_netuid
import numpy as np
from validator.utils.logging import get_logger

from validator.utils.weight_utils import process_weights_for_netuid

from validator.config import (
    NETUID, 
    SUBTENSOR_NETWORK, 
    SUBTENSOR_ADDRESS,
    WALLET_NAME,
    HOTKEY_NAME,
    VERSION_KEY,
    ALPHA_SCORING_MULTIPLICATOR,
    NO_RESPONSE_MIN_SCORE
)

def normalize(x: np.ndarray, p: int = 2, dim: int = 0) -> np.ndarray:
    """Normalize array using L-p norm"""
    norm = np.linalg.norm(x, ord=p, axis=dim, keepdims=True)
    return x / np.clip(norm, 1e-12, None)

logger = get_logger(__name__)

async def _set_weights_with_timeout(
    substrate,
    keypair,
    node_ids: List[int],
    node_weights: List[float],
    validator_node_id: int,
    version_key: int,
    timeout: float = 120.0  # 2 minutes timeout
) -> bool:
    """Wrapper to call set_node_weights with a timeout."""
    try:
        return await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None,
                weights.set_node_weights,
                substrate,
                keypair,
                node_ids,
                node_weights,
                NETUID,
                validator_node_id,
                version_key,
                True,  # wait_for_inclusion
                True,  # wait_for_finalization
            ),
            timeout=timeout
        )
    except asyncio.TimeoutError:
        logger.error(f"set_node_weights timed out after {timeout} seconds")
        return False
    except Exception as e:
        logger.error(f"Error in set_node_weights: {str(e)}")
        return False
    
def query_node_id(substrate: SubstrateInterface) -> int | None:
    keypair = chain_utils.load_hotkey_keypair(wallet_name=WALLET_NAME, hotkey_name=HOTKEY_NAME)
    node_id_query = substrate.query("SubtensorModule", "Uids", [NETUID, keypair.ss58_address])
    if node_id_query is None:
        logger.error(f"Failed to get validator node ID for {keypair.ss58_address}")
        return
    return node_id_query.value

def query_version_key(substrate: SubstrateInterface) -> int | None:
    version_key_query = substrate.query("SubtensorModule", "WeightsVersionKey", [NETUID])
    if version_key_query is None:
        logger.error(f"Failed to get subnet version key for {NETUID}")
        return
    return version_key_query.value


async def set_weights(best_miner_hotkey: str | None = None):
    """Set all validator weight to the miner identified by ``best_miner_hotkey``.

    The function **must** receive a hotkey.  If none is provided the call is
    aborted (no on-chain transaction is submitted).
    """
    try:
        keypair = chain_utils.load_hotkey_keypair(wallet_name=WALLET_NAME, hotkey_name=HOTKEY_NAME)
        substrate = interface.get_substrate(SUBTENSOR_NETWORK, SUBTENSOR_ADDRESS)

        validator_node_id = query_node_id(substrate)
        # version_key = query_version_key(substrate)
        # if version_key is None:
        #     # Fallback to static config value if on-chain query fails
        version_key = VERSION_KEY

        logger.info(f"Validator node ID: {validator_node_id}, Version key: {version_key}")

        if validator_node_id is None:
            logger.error("Failed to get validator node ID – aborting weight update")
            return

        nodes = get_nodes_for_netuid(substrate, NETUID)

        if best_miner_hotkey is None:
            logger.error("best_miner_hotkey must be provided for set_weights – aborting")
            return

        scores = np.zeros(len(nodes), dtype=np.float32)

        # Locate the node matching the provided hotkey (case-sensitive).
        hotkey_to_idx = {node.hotkey: idx for idx, node in enumerate(nodes)}

        target_idx = hotkey_to_idx.get(best_miner_hotkey)

        if target_idx is None:
            logger.error(f"Hotkey {best_miner_hotkey} not found among active nodes – aborting weight update")
            return

        scores[target_idx] = 1.0

        # Calculate the weights using L1 normalization
        raw_weights = normalize(scores, p=1, dim=0)

        # Create uids array
        uids = np.array([node.node_id for node in nodes])
        
        # Process weights using the centralized function
        try:
            node_ids, node_weights = process_weights_for_netuid(
                uids=uids,
                weights=raw_weights,
                netuid=NETUID,
                substrate=substrate,
                nodes=nodes,
                exclude_quantile=0
            )
        except Exception as e:
            logger.error(f"Failed to process weights with exception: {e}")
            return

        logger.info(
            f"Setting weights exclusively for hotkey {best_miner_hotkey} (uid={node_ids[0] if node_ids else 'N/A'})"
        )

        # Log the exact vector that will be submitted to the chain
        logger.info(f"Submitting weight vector: {list(zip(node_ids, node_weights))}")

        success = await _set_weights_with_timeout(
            substrate=substrate,
            keypair=keypair,
            node_ids=node_ids,
            node_weights=node_weights,
            validator_node_id=validator_node_id,
            version_key=version_key
        )

        if success:
            logger.info("Successfully set weights on chain")
        else:
            logger.error("Failed to set weights on chain")
    except Exception as e:
        logger.error(f"Error setting weights: {str(e)}")
        logger.exception("Full error traceback:")
        raise