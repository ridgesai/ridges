import asyncio
from typing import List

from fiber import SubstrateInterface
from fiber.chain import chain_utils, interface, metagraph, weights
from fiber.chain.fetch_nodes import get_nodes_for_netuid
import numpy as np
from shared.logging_utils import get_logger

from validator.dependencies import get_session_factory
from validator.sandbox.schema import AgentVersion, EvaluationRun
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

def get_latest_scores_by_hotkey() -> dict[str, float]:
    """Get the most recent score for each miner hotkey using SQLAlchemy directly."""
    SessionFactory = get_session_factory()
    session = SessionFactory()
    try:
        # Get the latest evaluation run for each agent version
        latest_runs = session.query(
            EvaluationRun.evaluation_id,
            EvaluationRun.solved,
            EvaluationRun.result_scored_at
        ).order_by(
            EvaluationRun.result_scored_at.desc()
        ).all()
        
        # Group by version_id to get the most recent run for each version
        latest_by_version = {}
        for run in latest_runs:
            if run.version_id not in latest_by_version:
                latest_by_version[run.version_id] = run
        
        # Get agent versions to map version_id to hotkey
        agent_versions = session.query(
            AgentVersion.version_id,
            AgentVersion.miner_hotkey
        ).filter(
            AgentVersion.version_id.in_([run.version_id for run in latest_by_version.values()])
        ).all()
        
        # Create mapping from version_id to hotkey
        version_to_hotkey = {av.version_id: av.miner_hotkey for av in agent_versions}
        
        # Create mapping from hotkey to latest score
        latest_scores = {}
        for version_id, run in latest_by_version.items():
            if version_id in version_to_hotkey:
                hotkey = version_to_hotkey[version_id]
                # Use solved status as score (1.0 if solved, 0.0 if not)
                score = 1.0 if run.solved else 0.0
                latest_scores[hotkey] = score
                logger.debug(f"Latest score for {hotkey}: {score} (solved: {run.solved})")
        
        return latest_scores
    finally:
        session.close()

async def set_weights():
    """
    Sets the validator weights to the metagraph hotkeys based on the most recent scores from the miners.
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

        # Get latest scores directly using SQLAlchemy
        latest_scores_by_hotkey = get_latest_scores_by_hotkey()
        scores = np.array([latest_scores_by_hotkey.get(node.hotkey, NO_RESPONSE_MIN_SCORE) for node in nodes])
        
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