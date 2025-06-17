"""Utility functions for checking node availability."""

import time
import asyncio
from fiber.chain.models import Node
from validator.db.operations import DatabaseManager
from validator.utils.node_utils import construct_server_address
from shared.logging_utils import get_logger
from validator.config import MAX_MINERS
import random
import httpx

logger = get_logger(__name__)

async def check_miner_availability(
    node: Node, 
    client: httpx.AsyncClient, 
    db_manager: DatabaseManager, 
    hotkey: str
) -> bool: 
    """Check if a miner is available and log the result."""
    server_address = await construct_server_address(node)
    start_time = time.time()
    
    try:
        headers = {"validator-hotkey": hotkey}
        response = await client.get(f"{server_address}/availability", headers=headers, timeout=5.0)
        response_time = (time.time() - start_time) * 1000  # Convert to milliseconds
        
        is_available = response.json().get("available", False)
        
        # Log availability check
        db_manager.log_availability_check(
            node_id=node.node_id,
            hotkey=node.hotkey,
            is_available=is_available,
            response_time_ms=response_time
        )
        
        return is_available
    except Exception as e:
        response_time = (time.time() - start_time) * 1000
        # Log failed check
        db_manager.log_availability_check(
            node_id=node.node_id,
            hotkey=node.hotkey,
            is_available=False,
            response_time_ms=response_time,
            error=str(e)
        )
        
        logger.warning(f"Failed to check availability for node {node.node_id}: {str(e)}")
        return False

async def get_available_nodes_with_api(
    nodes: list[Node],
    client: httpx.AsyncClient,
    db_manager: DatabaseManager,
    hotkey: str
) -> list[Node]:
    """Check availability of all nodes using their APIs and return available ones up to MAX_MINERS."""
    
    logger.info(f"Checking availability for all {len(nodes)} nodes")
    availability_tasks = [
        check_miner_availability(node, client, db_manager, hotkey)
        for node in nodes
    ]

    availability_results = await asyncio.gather(*availability_tasks)

    # Filter available nodes
    available_nodes = [
        node for node, is_available in zip(nodes, availability_results)
        if is_available
    ]

    total_available = len(available_nodes)
    logger.info(f"Found {total_available} available nodes out of {len(nodes)} total nodes")

    # If we have more available nodes than MAX_MINERS, randomly select MAX_MINERS
    selected_nodes = available_nodes
    if total_available > MAX_MINERS:
        logger.info(f"Randomly selecting {MAX_MINERS} nodes from {total_available} available nodes")
        selected_nodes = random.sample(available_nodes, MAX_MINERS)
    else:
        logger.info(f"Using all {total_available} available nodes (less than MAX_MINERS={MAX_MINERS})")
    
    # Log selected nodes
    for node in selected_nodes:
        logger.info(f"Selected node {node.node_id} (hotkey: {node.hotkey})")
    
    return selected_nodes 