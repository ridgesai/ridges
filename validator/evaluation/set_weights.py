import asyncio
from typing import List

from fiber import SubstrateInterface
from fiber.chain import chain_utils, interface, metagraph, weights
from fiber.chain.fetch_nodes import get_nodes_for_netuid
import numpy as np
from shared.logging_utils import get_logger
from validator.utils.injection_guard import is_banned

from validator.db.operations import DatabaseManager
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
from validator.db.operations import DatabaseManager
from validator.evaluation.log_score import ScoreLog, log_scores
from validator.evaluation.weight_utils import process_weights_for_netuid


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

def normalize(x: np.ndarray, p: int = 2, dim: int = 0) -> np.ndarray:
    norm = np.linalg.norm(x, ord=p, axis=dim, keepdims=True)
    return x / np.clip(norm, 1e-12, None)
    
async def set_weights(db_manager: DatabaseManager):
    """
    Sets the validator weights to the metagraph hotkeys based on the scores it has received from the miners.
    """
    try:
        keypair = chain_utils.load_hotkey_keypair(wallet_name=WALLET_NAME, hotkey_name=HOTKEY_NAME)
        substrate = interface.get_substrate(SUBTENSOR_NETWORK, SUBTENSOR_ADDRESS)

        validator_node_id = query_node_id(substrate)
        version_key = query_version_key(substrate)
        logger.info(f"Validator node ID: {validator_node_id}, Version key: {version_key}")
        if validator_node_id is None or version_key is None:
            logger.error("Failed to get validator node ID or version key")
            return

        nodes = get_nodes_for_netuid(substrate, NETUID)

        average_scores_by_hotkey = db_manager.get_average_scores_by_hotkey(hours=24)
        scores = np.array([
            0.0 if is_banned(node.hotkey) else average_scores_by_hotkey.get(node.hotkey, NO_RESPONSE_MIN_SCORE)
            for node in nodes
        ])
        
        # Calculate the average reward for each uid across non-zero values.
        # Compute the norm of the scores
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

        # Log weight information
        score_logs = []
        for node_id, weight in zip(node_ids, node_weights):
            # Find the corresponding node
            node = next((n for n in nodes if n.node_id == node_id), None)
            if node:
                score_logs.append(ScoreLog(type="weight", validator_hotkey=keypair.ss58_address, miner_hotkey=node.hotkey, score=weight))

        await log_scores(score_logs)

        logger.info(f"weights: {node_weights}")

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


async def set_weights_bayesian(
    db_manager: DatabaseManager,
    validator_hotkey: str
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
        node_id_query = substrate.query("SubtensorModule", "Uids", [NETUID, keypair.ss58_address])
        if node_id_query is None:
            logger.error(f"Failed to get validator node ID for {keypair.ss58_address}")
            return
        validator_node_id = node_id_query.value
        version_key_query = substrate.query("SubtensorModule", "WeightsVersionKey", [NETUID])
        if version_key_query is None:
            logger.error(f"Failed to get subnet version key for {NETUID}")
            return
        version_key = version_key_query.value
        logger.info(f"Subnet Version key: {version_key}")

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
            bayesian_score = 0.0 if is_banned(hotkey) else hotkey_to_bayesian_score.get(hotkey, 0.0)
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

        # Log detailed weight information
        score_logs = []
        logger.info(f"Setting weights for {len(nodes)} nodes")
        for node_id, weight, node in zip(node_ids, node_weights, nodes):
            hotkey = node.hotkey
            bayesian_score = 0.0 if is_banned(hotkey) else hotkey_to_bayesian_score.get(hotkey, 0.0)
            score_logs.append(ScoreLog(type="weight", validator_hotkey=validator_hotkey, miner_hotkey=hotkey, score=weight))
            
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
        await log_scores(score_logs)
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
