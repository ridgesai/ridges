# Python built in imports 
from pathlib import Path
import sys 
import time
import random
import os
import asyncio
from datetime import datetime, timezone
import uuid

# External package imports
from fiber.chain.interface import get_substrate
from fiber.chain.models import Node
from fiber.chain.chain_utils import load_hotkey_keypair
from fiber.chain.fetch_nodes import get_nodes_for_netuid
from dotenv import load_dotenv
import httpx
from openai import OpenAI

from validator.utils.is_cheating import is_cheating

# Load environment variables
validator_dir = Path(__file__).parent
env_path = validator_dir / ".env"
load_dotenv(env_path)

# Internal package imports
from validator.challenge.create_regression_challenge import create_next_regression_challenge
from validator.db.operations import DatabaseManager
from validator.challenge.common import ChallengeTask
from validator.challenge.create_codegen_challenge import create_next_codegen_challenge
from shared.logging_utils import get_logger, logging_update_active_coroutines
from validator.config import (
    NETUID, SUBTENSOR_NETWORK, SUBTENSOR_ADDRESS,
    WALLET_NAME, HOTKEY_NAME, CHALLENGE_INTERVAL,
    CHALLENGE_TIMEOUT, DB_PATH, WEIGHTS_INTERVAL,
    MAX_MINERS, MIN_MINERS, LOG_DRAIN_FREQUENCY, RIDGES_API_URL,
    VERSION_COMMIT_HASH
)
from validator.evaluation.evaluation_loop import run_evaluation_loop
from validator.utils.async_utils import AsyncBarrier
from validator.evaluation.set_weights import set_weights

project_root = str(Path(__file__).resolve().parents[2])
sys.path.append(project_root)

# Set up logger
logger = get_logger(__name__)
logger.info(f"Loading environment variables from {env_path.absolute()}")

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

async def weights_update_loop(db_manager: DatabaseManager, validator_hotkey: str) -> None:
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

async def post_to_ridges_api(db_manager: DatabaseManager, validator_hotkey: str):
    # TODO: Post data with fiber to show validator signature
    logger.info("Starting Ridges API drain loop")
    consecutive_failures = 0
    max_consecutive_failures = 3

    while True:
        try:
            start_time = time.time()

            # Fetch all logs created in the last n minutes (based on the Config)
            tasks = [
                db_manager.get_all_challenge_table_entries("codegen_challenges"),
                db_manager.get_all_response_table_entries("codegen_responses"), 
                db_manager.get_all_challenge_table_entries("regression_challenges"),
                db_manager.get_all_response_table_entries("regression_responses"), 
            ]
            codegen_challenges = tasks[0]
            codegen_responses = tasks[1]
            regression_challenges = tasks[2]
            regression_responses = tasks[3]

            logger.info(f"Fetched {len(codegen_challenges)} codegen challenges, {len(codegen_responses)} codegen responses, {len(regression_challenges)} regression challenges, {len(regression_responses)} regression responses from database. Preparing to post to Ridges API")
            async with httpx.AsyncClient() as client:
                api_tasks = []
                if (len(codegen_challenges) > 0):
                    api_tasks.append(
                        client.post(
                            f"{RIDGES_API_URL}/ingestion/codegen-challenges?validator_version={VERSION_COMMIT_HASH}&validator_hotkey={validator_hotkey}",
                            json=codegen_challenges
                        )
                    )
                if (len(codegen_responses) > 0):
                    api_tasks.append(
                        client.post(
                            f"{RIDGES_API_URL}/ingestion/codegen-responses?validator_version={VERSION_COMMIT_HASH}&validator_hotkey={validator_hotkey}",
                            json=codegen_responses
                        )
                    )
                if (len(regression_challenges) > 0):
                    api_tasks.append(
                        client.post(
                            f"{RIDGES_API_URL}/ingestion/regression-challenges?validator_version={VERSION_COMMIT_HASH}&validator_hotkey={validator_hotkey}",
                            json=regression_challenges
                        )
                    )
                if (len(regression_responses) > 0):
                    api_tasks.append(
                        client.post(
                            f"{RIDGES_API_URL}/ingestion/regression-responses?validator_version={VERSION_COMMIT_HASH}&validator_hotkey={validator_hotkey}",
                            json=regression_responses
                        )
                    )
                await asyncio.gather(*api_tasks)

            # Calculate how long the loop took 
            elapsed_time = time.time() - start_time
            sleep_time = max(0, LOG_DRAIN_FREQUENCY.total_seconds() - elapsed_time)

            # Sleep for the remaining time
            logger.info(f"Posted to Ridges API, sleeping for {sleep_time} seconds")
            await asyncio.sleep(sleep_time)
        except Exception as e:
            consecutive_failures += 1
            logger.error(f"Error in drain api loop (attempt {consecutive_failures}/{max_consecutive_failures}): {str(e)}")

            if consecutive_failures >= max_consecutive_failures:
                logger.error("Too many consecutive failures in weights update loop, waiting for longer period")
                logging_update_active_coroutines("weights_task", False)
                await asyncio.sleep(WEIGHTS_INTERVAL.total_seconds() * 2)  # Wait twice as long before retrying
                consecutive_failures = 0  # Reset counter after long wait
            else:
                # Wait normal interval before retry
                logging_update_active_coroutines("weights_task", False)
                await asyncio.sleep(WEIGHTS_INTERVAL.total_seconds())

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
                sleep_interval=5 * 60 # 5 minutes
            )
        )
        evaluation_task.add_done_callback(
            lambda t: logger.error(f"Evaluation task ended unexpectedly: {t.exception()}")
            if t.exception() else None
        )

        # Start weights update loop as a separate task
        logger.info("Starting weights update task...")
        weights_task = asyncio.create_task(weights_update_loop(db_manager, validator_hotkey=hotkey.ss58_address))
        weights_task.add_done_callback(
            lambda t: logger.error(f"Weights task ended unexpectedly: {t.exception()}")
            if t.exception() else None
        )

        # Start a task to periodically push non sensitive data to Ridges for the dashboard
        logger.info("Starting Ridges API task...")
        api_drain_task = asyncio.create_task(post_to_ridges_api(db_manager, validator_hotkey=hotkey.ss58_address))
        api_drain_task.add_done_callback(
            lambda t: logger.error(f"Ridges API task ended unexpectedly: {t.exception()}")
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
                    for task in [evaluation_task, weights_task, api_drain_task]:
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
                                    weights_task = asyncio.create_task(weights_update_loop(db_manager, validator_hotkey=hotkey.ss58_address))
                                elif task == api_drain_task:
                                    logger.info("Restarting API drain task...")
                                    api_drain_task = asyncio.create_task(post_to_ridges_api(db_manager, validator_hotkey=hotkey.ss58_address))
                    
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
                    challenge = await create_next_codegen_challenge(hotkey.ss58_address, openai_client)

                    if not challenge:
                        logger.info(f"Sleeping for {CHALLENGE_INTERVAL.total_seconds()} seconds before next challenge check...")
                        await asyncio.sleep(CHALLENGE_INTERVAL.total_seconds())
                        continue

                    logger.info(f"Processing challenge: task_id={challenge.challenge_id}")

                    # Log background task status
                    logger.info("Background task status:")
                    logger.info(f"  - Evaluation task running: {not evaluation_task.done()}")
                    logger.info(f"  - Weights task running: {not weights_task.done()}")
                    logger.info(f"  - API drain task running: {not api_drain_task.done()}")

                    for node in available_nodes:
                        server_address = await construct_server_address(node)
                        
                        task = asyncio.create_task(
                            challenge.send(
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
            api_drain_task.cancel()
            try:
                await asyncio.gather(evaluation_task, weights_task, api_drain_task, return_exceptions=True)
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
