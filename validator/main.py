# Python built in imports 
from pathlib import Path
import sys 
import time
from typing import Optional
import random
import os
import asyncio
from datetime import datetime, timezone

# External package imports
from fiber.chain.interface import get_substrate
from fiber.chain.models import Node
from fiber.chain.chain_utils import load_hotkey_keypair
from fiber.chain.fetch_nodes import get_nodes_for_netuid
from dotenv import load_dotenv
import httpx
from openai import OpenAI

# Internal package imports
from validator.db.operations import DatabaseManager
from validator.challenge.challenge_types import ChallengeTask
from validator.challenge.create_codegen_challenge import create_next_codegen_challenge
from validator.challenge.send_codegen_challenge import send_challenge
from shared.logging_utils import get_logger, logging_update_active_coroutines
from validator.config import (
    NETUID, SUBTENSOR_NETWORK, SUBTENSOR_ADDRESS,
    WALLET_NAME, HOTKEY_NAME, CHALLENGE_INTERVAL,
    CHALLENGE_TIMEOUT, DB_PATH, WEIGHTS_INTERVAL,
    MAX_MINERS, MIN_MINERS
)
from validator.evaluation.evaluation_loop import run_evaluation_loop
from validator.utils.async_utils import AsyncBarrier
from validator.evaluation.set_weights import set_weights

project_root = str(Path(__file__).resolve().parents[2])
sys.path.append(project_root)

# Load environment variables
validator_dir = Path(__file__).parent
env_path = validator_dir / ".env"
load_dotenv(env_path)

# Set up logger
logger = get_logger(__name__)

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

async def weights_update_loop(db_manager: DatabaseManager) -> None:
    """Run the weights update loop on WEIGHTS_INTERVAL."""
    logger.info("Starting weights update loop")
    consecutive_failures = 0
    max_consecutive_failures = 3

    while True: 
        logging_update_active_coroutines("weights_task", True)
        try: 
            await set_weights(db_manager)
            consecutive_failures = 0 # Reset failure counter on success
            logger.info(f"Weights updated successfully, sleeping for {WEIGHTS_INTERVAL}")
            logging_update_active_coroutines("weights_task", False)
            await asyncio.sleep(WEIGHTS_INTERVAL.total_seconds())
        except Exception as e:
            consecutive_failures += 1
            logger.error(f"Error in weights update loop (attempt {consecutive_failures}/{max_consecutive_failures}): {str(e)}")
            
            if consecutive_failures >= max_consecutive_failures:
                logger.error("Too many consecutive failures in weights update loop, waiting for longer period")
                logging_update_active_coroutines("weights_task", False)
                await asyncio.sleep(WEIGHTS_INTERVAL.total_seconds() * 2)  # Wait twice as long before retrying
                consecutive_failures = 0  # Reset counter after long wait
            else:
                # Wait normal interval before retry
                logging_update_active_coroutines("weights_task", False)
                await asyncio.sleep(WEIGHTS_INTERVAL.total_seconds())

async def periodic_cleanup(db_manager: DatabaseManager, interval_hours: int = 24):
    logger.info("Not cleaning anything up until push to main db instance implemented")
    return 

async def main():
    """Main validator loop."""
    
    # Load env vars and make sure they are present
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY environment variable not set")
    
    openai_client = OpenAI(api_key=OPENAI_API_KEY)

    # Load validator hotkey
    try:
        hotkey = load_hotkey_keypair(WALLET_NAME, HOTKEY_NAME)
    except Exception as e:
        logger.error(f"Failed to load keys: {str(e)}")
        return
    
     # Initialize database manager and validator
    logger.info(f"Initializing database manager with path: {DB_PATH}")
    db_manager = DatabaseManager(DB_PATH)
    # Initialize HTTP client with long timeout
    async with httpx.AsyncClient(timeout=CHALLENGE_TIMEOUT.total_seconds()) as client:
        active_challenge_tasks = []  # Track active challenges

        # Start evaluation loop as a separate task
        logger.info("Starting evaluation loop task...")
        evaluation_task = asyncio.create_task(
            run_evaluation_loop(
                db_path=DB_PATH,
                openai_client=openai_client,
                validator_hotkey=hotkey.ss58_address,
                sleep_interval=15
            )
        )
        evaluation_task.add_done_callback(
            lambda t: logger.error(f"Evaluation task ended unexpectedly: {t.exception()}")
            if t.exception() else None
        )

        # Start weights update loop as a separate task
        logger.info("Starting weights update task...")
        weights_task = asyncio.create_task(weights_update_loop(db_manager))
        weights_task.add_done_callback(
            lambda t: logger.error(f"Weights task ended unexpectedly: {t.exception()}")
            if t.exception() else None
        )
    
        # Start the periodic cleanup task
        logger.info("Starting cleanup task...")
        cleanup_task = asyncio.create_task(periodic_cleanup(db_manager))
        cleanup_task.add_done_callback(
            lambda t: logger.error(f"Cleanup task ended unexpectedly: {t.exception()}")
            if t.exception() else None
        )

        # runs the main iteration loop within async client
        try: 
            # Main challenge loop
            iteration = 0 
            while True: 
                try:
                    iteration += 1

                    logger.info(f"Main loop iteration {iteration}")

                    # Check if any background tasks failed
                    for task in [evaluation_task, weights_task, cleanup_task]:
                        if task.done() and not task.cancelled():
                            exc = task.exception()
                            if exc:
                                logger.error(f"Background task failed: {exc}")
                                # Restart the failed task
                                if task == evaluation_task:
                                    logger.info("Restarting evaluation loop...")
                                    evaluation_task = asyncio.create_task(
                                        run_evaluation_loop(
                                            db_path=DB_PATH,
                                            openai_client=openai_client,
                                            validator_hotkey=hotkey.ss58_address,
                                            sleep_interval=10
                                        )
                                    )
                                elif task == weights_task:
                                    logger.info("Restarting weights update loop...")
                                    weights_task = asyncio.create_task(weights_update_loop(db_manager))
                                elif task == cleanup_task:
                                    logger.info("Restarting cleanup task...")
                                    cleanup_task = asyncio.create_task(periodic_cleanup(db_manager))
                    
                    # Clean up completed challenge tasks
                    active_challenge_tasks = [
                        task for task in active_challenge_tasks 
                        if not task.task.done()
                    ]
                    
                    # Get active nodes and make sure we have enough to continue the loop
                    active_nodes = get_active_nodes_on_chain()
                    num_active = len(active_nodes)

                    if num_active < MIN_MINERS:
                        logger.warning(f"Only {num_active} active nodes available (minimum {MIN_MINERS} required)")
                        logger.info(f"Will check again in {CHALLENGE_INTERVAL.total_seconds()} seconds...")
                        await asyncio.sleep(CHALLENGE_INTERVAL.total_seconds())
                        continue
                
                    # Check availability of nodes
                    available_nodes = await get_available_nodes_with_api(active_nodes, client, db_manager, hotkey.ss58_address)
                    num_available = len(available_nodes)
                    
                    if num_available < MIN_MINERS:
                        logger.warning(f"Only {num_available} nodes are available (minimum {MIN_MINERS} required)")
                        logger.info(f"Sleeping for {CHALLENGE_INTERVAL.total_seconds()} seconds before next availability check...")
                        await asyncio.sleep(CHALLENGE_INTERVAL.total_seconds())
                        continue

                    logger.info(f"Processing {num_available} available nodes")

                    # Generate and send challenges
                    new_challenge_tasks = []
                    barrier = AsyncBarrier(parties=len(available_nodes))

                    
                    # Fetch next challenge from API with retries
                    challenge = await create_next_codegen_challenge(openai_client)

                    if not challenge:
                        logger.info(f"Sleeping for {CHALLENGE_INTERVAL.total_seconds()} seconds before next challenge check...")
                        await asyncio.sleep(CHALLENGE_INTERVAL.total_seconds())
                        continue

                    logger.info(f"Processing challenge: task_id={challenge.challenge_id}")

                    # Log background task status
                    logger.info("Background task status:")
                    logger.info(f"  - Evaluation task running: {not evaluation_task.done()}")
                    logger.info(f"  - Weights task running: {not weights_task.done()}")
                    logger.info(f"  - Cleanup task running: {not cleanup_task.done()}")

                    for node in available_nodes:
                        server_address = await construct_server_address(node)
                        
                        task = asyncio.create_task(
                            send_challenge(
                                challenge=challenge,
                                server_address=server_address,
                                hotkey=node.hotkey,
                                keypair=hotkey,
                                node_id=node.node_id,
                                barrier=barrier,
                                db_manager=db_manager,
                                client=client
                            )
                        )
                        
                        challenge_task = ChallengeTask(
                            node_id=node.node_id,
                            task=task,
                            timestamp=datetime.now(timezone.utc),
                            challenge=challenge,
                            miner_hotkey=node.hotkey
                        )
                        new_challenge_tasks.append(challenge_task)
                    
                    # Add new challenges to active tasks
                    active_challenge_tasks.extend(new_challenge_tasks)

                    # NOTE: In the background, the evaluation loop looks at pending responses and evaluates them.

                    # Log status
                    num_active_challenges = len(active_challenge_tasks)
                    if num_active_challenges > 0:
                        logger.info(f"Currently tracking {num_active_challenges} active challenges")

                    # Sleep until next challenge interval
                    await asyncio.sleep(CHALLENGE_INTERVAL.total_seconds())
                except KeyboardInterrupt:
                    break
                except Exception as e: 
                    logger.error(f"Error in main loop: {str(e)}")
                    await asyncio.sleep(CHALLENGE_INTERVAL.total_seconds())
        finally: 
            # Cancel evaluation and weights loops
            evaluation_task.cancel()
            weights_task.cancel()
            cleanup_task.cancel()
            try:
                await asyncio.gather(evaluation_task, weights_task, cleanup_task, return_exceptions=True)
            except asyncio.CancelledError:
                pass
        
        # Cleanup
        if db_manager:
            db_manager.close()




# Lastly the if name runs it all and then exists out on keyboard interrupt
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
