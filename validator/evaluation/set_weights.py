import asyncio
from typing import List

from fiber.chain import chain_utils, interface, metagraph, weights
from fiber.chain.fetch_nodes import get_nodes_for_netuid
from fiber.logging_utils import get_logger


from validator.db.operations import DatabaseManager
from validator.config import (
    NETUID, 
    SUBTENSOR_NETWORK, 
    SUBTENSOR_ADDRESS,
    WALLET_NAME,
    HOTKEY_NAME,
    VERSION_KEY,
    ALPHA_SCORING_MULTIPLICATOR
)
from validator.db.operations import DatabaseManager

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

async def set_weights(
    db_manager: DatabaseManager
) -> None:
    """Set weights for miners based on their performance scores from the last 24 hours."""
    try:
        # Get substrate and keypair
        substrate = interface.get_substrate(
            subtensor_network=SUBTENSOR_NETWORK,
            subtensor_address=SUBTENSOR_ADDRESS
        )
        keypair = chain_utils.load_hotkey_keypair(wallet_name=WALLET_NAME, hotkey_name=HOTKEY_NAME)
        
        # Get validator node ID and version key
        validator_node_id = substrate.query("SubtensorModule", "Uids", [NETUID, keypair.ss58_address]).value
        v_key = substrate.query("SubtensorModule", "WeightsVersionKey", [NETUID]).value
        logger.info(f"Subnet Version key: {v_key}")
        version_key = VERSION_KEY
        # Get all active nodes
        nodes = get_nodes_for_netuid(substrate=substrate, netuid=NETUID)
        
        # Get global average score and number of responses from database
        global_average, average_response_count = db_manager.get_global_miner_scores()
        bayesian_miner_scores = db_manager.get_bayesian_miner_score(
            global_average=global_average,
            average_count=average_response_count,
        )

        # Create mapping of miner hotkey to a Bayesian score
        hotkey_to_bayesian_score = {}
        for score in bayesian_miner_scores:
            miner_hotkey, response_count, average_score, bayesian_average = score
            hotkey_to_bayesian_score[miner_hotkey] = bayesian_average
            logger.debug(f"Miner {miner_hotkey}: responses={response_count}, avg={average_score:.4f}, bayesian={bayesian_average:.4f}")

        
        # Calculate weights
        node_weights: List[float] = []
        node_ids: List[int] = []

        for node in nodes:
            node_id = node.node_id
            hotkey = node.hotkey

            # Get Bayesian score for this node, default to 0 if not found
            bayesian_score = hotkey_to_bayesian_score.get(hotkey, 0.0)
            adjusted_score = bayesian_score ** (3 * ALPHA_SCORING_MULTIPLICATOR)

            node_ids.append(node_id)
            node_weights.append(adjusted_score)

        # Ensure weights sum to 1.0
        total_weight = sum(node_weights)
        if total_weight > 0:
            node_weights = [w / total_weight for w in node_weights]
        else:
            # If no scores, distribute evenly
            node_weights = [1.0 / len(nodes) for _ in nodes]

        # Breakpoint: code below from template
        
        # Ensure weights sum to 1.0
        total_weight = sum(node_weights)
        if total_weight > 0:
            node_weights = [w / total_weight for w in node_weights]
        else:
            # If no scores, distribute evenly
            node_weights = [1.0 / len(nodes) for _ in nodes]
        
        # Log detailed weight information
        logger.info(f"Setting weights for {len(nodes)} nodes")
        for node_id, weight, node in zip(node_ids, node_weights, nodes):
            hotkey = node.hotkey
            bayesian_score = hotkey_to_bayesian_score.get(hotkey, 0.0)
            
            # Get additional info if available
            miner_info = next((s for s in bayesian_miner_scores if s[0] == hotkey), None)
            if miner_info:
                _, response_count, avg_score, _ = miner_info
                logger.info(
                    f"Node {node_id} ({hotkey}): "
                    f"responses={response_count}, "
                    f"avg_score={avg_score:.4f}, "
                    f"bayesian={bayesian_score:.4f}, "
                    f"weight={weight:.4f}"
                )
            else:
                logger.info(
                    f"Node {node_id} ({hotkey}): "
                    f"No responses in last 24h, "
                    f"weight={weight:.4f}"
                )
        
        # Set weights on chain with timeout
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