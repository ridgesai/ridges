"""Utility functions for handling node operations."""

from fiber.chain.interface import get_substrate
from fiber.chain.models import Node
from fiber.chain.fetch_nodes import get_nodes_for_netuid
from loggers.logging_utils import get_logger
from validator.config import (
    NETUID, SUBTENSOR_NETWORK, SUBTENSOR_ADDRESS
)
import random
from ddtrace import tracer

logger = get_logger(__name__)

@tracer.wrap(resource="construct-server-address")
async def construct_server_address(node: Node) -> str:
    """Construct server address for a node.
    
    For local development:
    - Nodes register as 0.0.0.1 on the chain (since 127.0.0.1 is not allowed)
    - But we connect to them via 127.0.0.1 locally
    """
    if node.ip == "0.0.0.1":
        # For local development, connect via localhost
        return f"http://127.0.0.1:{node.port}"
    return f"http://{node.ip}:{node.port}"

@tracer.wrap(resource="get-active-nodes-on-chain")
def get_active_nodes_on_chain() -> list[Node]:
    """This gets the miners registered as active on chain"""
    try:
        # Get nodes from chain
        substrate = get_substrate(
            subtensor_network=SUBTENSOR_NETWORK,
            subtensor_address=SUBTENSOR_ADDRESS
        )
        
        active_nodes = get_nodes_for_netuid(substrate, NETUID)
        logger.info(f"Found {len(active_nodes)} total nodes on chain")
        
        # Log details about active nodes
        logger.info(f"Found {len(active_nodes)} nodes on chain")
        for node in active_nodes:
            logger.info(f"Active node id: {node.node_id} hotkey: {node.hotkey}, ip: {node.ip}, port: {node.port}, last_updated: {node.last_updated}")
        
        # Return all active nodes without MAX_MINERS filtering
        return active_nodes
        
    except Exception as e:
        logger.error(f"Failed to get active nodes: {str(e)}", exc_info=True)
        return [] 