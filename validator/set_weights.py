# This code is genuinely horrendous.
# It is copied from the old repository. We have to fix it eventually.
# It is way too needlessly complicated.


import numpy as np
from typing import Tuple, List, Optional

import utils.logger as logger

from fiber import SubstrateInterface
from fiber.chain.fetch_nodes import get_nodes_for_netuid




U32_MAX = 4294967295
U16_MAX = 65535

def normalize_max_weight(
        x: np.ndarray, limit: float = 0.1
) -> np.ndarray:
    r"""Normalizes the numpy array x so that sum(x) = 1 and the max value is not greater than the limit.
    Args:
        x (:obj:`np.ndarray`):
            Array to be max_value normalized.
        limit: float:
            Max value after normalization.
    Returns:
        y (:obj:`np.ndarray`):
            Normalized x array.
    """
    epsilon = 1e-7  # For numerical stability after normalization

    weights = x.copy()
    values = np.sort(weights)

    if x.sum() == 0 or len(x) * limit <= 1:
        return np.ones_like(x) / x.size
    else:
        estimation = values / values.sum()

        if estimation.max() <= limit:
            return weights / weights.sum()

        # Find the cumulative sum and sorted array
        cumsum = np.cumsum(estimation, 0)

        # Determine the index of cutoff
        estimation_sum = np.array(
            [(len(values) - i - 1) * estimation[i] for i in range(len(values))]
        )
        n_values = (estimation / (estimation_sum + cumsum + epsilon) < limit).sum()

        # Determine the cutoff based on the index
        cutoff_scale = (limit * cumsum[n_values - 1] - epsilon) / (
                1 - (limit * (len(estimation) - n_values))
        )
        cutoff = cutoff_scale * values.sum()

        # Applying the cutoff
        weights[weights > cutoff] = cutoff

        y = weights / weights.sum()

        return y

def convert_weights_and_uids_for_emit(
        uids: np.ndarray, weights: np.ndarray
) -> Tuple[List[int], List[int]]:
    r"""Converts weights into integer u32 representation that sum to MAX_INT_WEIGHT.
    Args:
        uids (:obj:`np.ndarray,`):
            Array of uids as destinations for passed weights.
        weights (:obj:`np.ndarray,`):
            Array of weights.
    Returns:
        weight_uids (List[int]):
            Uids as a list.
        weight_vals (List[int]):
            Weights as a list.
    """
    # Checks.
    uids = np.asarray(uids)
    weights = np.asarray(weights)

    # Get non-zero weights and corresponding uids
    non_zero_weights = weights[weights > 0]
    non_zero_weight_uids = uids[weights > 0]

    # Debugging information
    logger.debug(f"weights: {weights}")
    logger.debug(f"non_zero_weights: {non_zero_weights}")
    logger.debug(f"uids: {uids}")
    logger.debug(f"non_zero_weight_uids: {non_zero_weight_uids}")

    if np.min(weights) < 0:
        raise ValueError(
            "Passed weight is negative cannot exist on chain {}".format(weights)
        )
    if np.min(uids) < 0:
        raise ValueError("Passed uid is negative cannot exist on chain {}".format(uids))
    if len(uids) != len(weights):
        raise ValueError(
            "Passed weights and uids must have the same length, got {} and {}".format(
                len(uids), len(weights)
            )
        )
    if np.sum(weights) == 0:
        logger.debug("nothing to set on chain")
        return [], []  # Nothing to set on chain.
    else:
        # Normalize weights to sum to 1
        weight_sum = float(np.sum(weights))
        weights = [
            float(value) / weight_sum for value in weights
        ]  # Normalize so weights sum to 1
        logger.debug(f"setting on chain sum: {weight_sum} and normalized weights (sum={sum(weights)}): {weights}")

    weight_vals = []
    weight_uids = []
    for i, (weight_i, uid_i) in enumerate(list(zip(weights, uids))):
        uint16_val = round(
            float(weight_i) * int(U16_MAX)
        )  # convert to int representation.

        # Filter zeros
        if uint16_val != 0:  # Filter zeros
            weight_vals.append(uint16_val)
            weight_uids.append(uid_i)
    logger.debug(f"final params: {weight_uids} : {weight_vals}")
    return weight_uids, weight_vals


def process_weights_for_netuid(
        uids,
        weights: np.ndarray,
        netuid: int,
        substrate: SubstrateInterface,
        nodes: Optional[List] = None,
        exclude_quantile: int = 0,
) -> Tuple[List[int], List[float]]:
    """
    Complete weight processing pipeline that returns ready-to-use node IDs and weights for chain submission.
    
    Args:
        uids: Array of node UIDs
        weights: Array of raw weights 
        netuid: Network UID
        substrate: Substrate interface for queries
        nodes: List of nodes (optional, will be fetched if None)
        exclude_quantile: Quantile to exclude (default 0)
        
    Returns:
        Tuple of (node_ids: List[int], node_weights: List[float]) ready for chain submission
    """
    logger.debug("process_weights_for_netuid()")
    logger.debug(f"weights: {weights}")
    logger.debug(f"netuid: {netuid}")
    logger.debug(f"substrate: {substrate}")
    logger.debug(f"nodes: {nodes}")

    # Get latest nodes from chain if nodes is None.
    if nodes is None:
        nodes = get_nodes_for_netuid(substrate, netuid)

    # Cast weights to floats.
    if not isinstance(weights, np.ndarray) or weights.dtype != np.float32:
        weights = weights.astype(np.float32)

    # Network configuration parameters from substrate.
    # These parameters determine the range of acceptable weights for each neuron.
    quantile = exclude_quantile / U16_MAX
    
    # Query network parameters using fiber substrate
    min_allowed_weights_query = substrate.query("SubtensorModule", "MinAllowedWeights", [netuid])
    max_weight_limit_query = substrate.query("SubtensorModule", "MaxWeightsLimit", [netuid])
    
    min_allowed_weights = min_allowed_weights_query.value if min_allowed_weights_query else 8
    max_weight_limit = max_weight_limit_query.value / U16_MAX if max_weight_limit_query else 0.1
    
    logger.debug(f"quantile: {quantile}")
    logger.debug(f"min_allowed_weights: {min_allowed_weights}")
    logger.debug(f"max_weight_limit: {max_weight_limit}")

    # Find all non zero weights.
    non_zero_weight_idx = np.argwhere(weights > 0).squeeze()
    non_zero_weight_idx = np.atleast_1d(non_zero_weight_idx)
    non_zero_weight_uids = uids[non_zero_weight_idx]
    non_zero_weights = weights[non_zero_weight_idx]
    
    if non_zero_weights.size == 0 or len(nodes) < min_allowed_weights:
        logger.warning("No non-zero weights returning all ones.")
        final_weights = np.ones(len(nodes)) / len(nodes)
        processed_weight_uids = np.arange(len(final_weights))
        processed_weights = final_weights
        
    elif non_zero_weights.size < min_allowed_weights:
        logger.warning("No non-zero weights less then min allowed weight, returning all ones.")
        temp_weights = np.ones(len(nodes)) * 1e-5  # creating minimum even non-zero weights
        temp_weights[non_zero_weight_idx] += non_zero_weights
        processed_weights = normalize_max_weight(x=temp_weights, limit=max_weight_limit)
        processed_weight_uids = np.arange(len(processed_weights))
        
    else:
        # Compute the exclude quantile and find the weights in the lowest quantile
        max_exclude = max(0, len(non_zero_weights) - min_allowed_weights) / len(non_zero_weights)
        exclude_quantile = min([quantile, max_exclude])
        lowest_quantile = np.quantile(non_zero_weights, exclude_quantile)
        
        logger.debug(f"max_exclude: {max_exclude}")
        logger.debug(f"exclude_quantile: {exclude_quantile}")
        logger.debug(f"lowest_quantile: {lowest_quantile}")

        # Exclude all weights below the allowed quantile.
        non_zero_weight_uids = non_zero_weight_uids[lowest_quantile <= non_zero_weights]
        non_zero_weights = non_zero_weights[lowest_quantile <= non_zero_weights]
        
        logger.debug(f"non_zero_weight_uids: {non_zero_weight_uids}")
        logger.debug(f"non_zero_weights: {non_zero_weights}")

        # Normalize weights
        processed_weights = normalize_max_weight(x=non_zero_weights, limit=max_weight_limit)
        processed_weight_uids = non_zero_weight_uids

    logger.debug(f"processed_weights: {processed_weights}")
    logger.debug(f"processed_weight_uids: {processed_weight_uids}")
    
    # Convert to uint16 weights and uids
    uint_uids, uint_weights = convert_weights_and_uids_for_emit(
        uids=processed_weight_uids, 
        weights=processed_weights
    )
    
    logger.debug(f"uint_weights: {uint_weights}")
    logger.debug(f"uint_uids: {uint_uids}") 
    
    # Convert back to float weights for setting on chain
    node_weights = [float(w) / float(U16_MAX) for w in uint_weights]
    node_ids = [int(uid) for uid in uint_uids]
    
    return node_ids, node_weights












import asyncio
from typing import List

from fiber import SubstrateInterface
from fiber.chain import chain_utils, interface, metagraph, weights
from fiber.chain.fetch_nodes import get_nodes_for_netuid
import numpy as np

from validator.config import (
    NETUID, 
    SUBTENSOR_NETWORK, 
    SUBTENSOR_ADDRESS,
    WALLET_NAME,
    HOTKEY_NAME,
    VERSION_KEY,
)


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


async def set_weights_from_mapping(weights_mapping: dict[str, float]):
    """Set validator weights according to a mapping of hotkey to weight.
    
    Args:
        weights_mapping: Dictionary mapping hotkey strings to float weights
    """
    try:
        keypair = chain_utils.load_hotkey_keypair(wallet_name=WALLET_NAME, hotkey_name=HOTKEY_NAME)
        substrate = interface.get_substrate(SUBTENSOR_NETWORK, SUBTENSOR_ADDRESS)

        validator_node_id = query_node_id(substrate)
        version_key = VERSION_KEY

        logger.info(f"Validator node ID: {validator_node_id}, Version key: {version_key}")

        if validator_node_id is None:
            logger.error("Failed to get validator node ID – aborting weight update")
            return

        nodes = get_nodes_for_netuid(substrate, NETUID)
        scores = np.zeros(len(nodes), dtype=np.float32)

        # Create mapping from hotkey to node index
        hotkey_to_idx = {node.hotkey: idx for idx, node in enumerate(nodes)}

        # Set scores according to the weights mapping
        for hotkey, weight in weights_mapping.items():
            target_idx = hotkey_to_idx.get(hotkey)
            if target_idx is not None:
                scores[target_idx] = weight
                logger.info(f"Setting weight {weight} for hotkey {hotkey}")
            else:
                logger.warning(f"Hotkey {hotkey} not found among active nodes – skipping")

        # Create uids array
        uids = np.array([node.node_id for node in nodes])
        
        if abs(sum(scores) - 1.0) > 1e-6:
            logger.warning(f"Sum of weights is not 1.0: {sum(scores)}")

        # Process weights using the centralized function
        try:
            node_ids, node_weights = process_weights_for_netuid(
                uids=uids,
                weights=scores,
                netuid=NETUID,
                substrate=substrate,
                nodes=nodes,
                exclude_quantile=0
            )
        except Exception as e:
            logger.error(f"Failed to process weights with exception: {e}")
            return

        logger.info(f"Setting weights for {len(weights_mapping)} hotkeys")

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