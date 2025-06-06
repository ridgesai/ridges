# Python built in imports 
from pathlib import Path
import sys 
import time
import random
import os
import asyncio
import tempfile
from zipfile import ZipFile
from datetime import datetime, timezone
import shutil
# External package imports
from fiber.chain.interface import get_substrate
from fiber.chain.models import Node
from fiber.chain.chain_utils import load_hotkey_keypair
from fiber.chain.fetch_nodes import get_nodes_for_netuid
from dotenv import load_dotenv
import httpx
from openai import OpenAI
from traceback import format_exception

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
    MAX_MINERS, MIN_MINERS, RIDGES_API_URL
)
from validator.evaluation.evaluation_loop import run_evaluation_loop
from validator.utils.async_utils import AsyncBarrier
from validator.evaluation.set_weights import set_weights
from validator.sandbox.manager import SandboxManager

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
            # Create a sandbox manager
            sbox_manager = SandboxManager()
            
            # Main challenge loop
            iteration = 0 
            while True: 
                try:
                    iteration += 1

                    logger.info(f"Main loop iteration {iteration}")

                    # Check if any background tasks failed
                    for task in [weights_task, cleanup_task]:
                        if task.done() and not task.cancelled():
                            exc = task.exception()
                            if exc:
                                logger.error(f"Background task failed: {exc}")
                                # Restart the failed task
                                if task == weights_task:
                                    logger.info("Restarting weights update loop...")
                                    weights_task = asyncio.create_task(weights_update_loop(db_manager))
                                elif task == cleanup_task:
                                    logger.info("Restarting cleanup task...")
                                    cleanup_task = asyncio.create_task(periodic_cleanup(db_manager))

                    # Fetch agents for a given task type 
                    task = "codegen" # Currently hardcoding to codegen. Will create concurrent tasks or some looping structure with regression integration
                    
                    # Get list of agents from RIDGES API
                    response = await client.get(f"{RIDGES_API_URL}/validator/get/agents", params={"type": task})
                    response.raise_for_status()
                    agents = response.json()['agents']

                    # Generate and send challenges
                    challenge = await create_next_codegen_challenge(openai_client)

                    # If there is an error generating the challenge, wait for a bit and rerun the loop
                    if not challenge:
                        logger.info(f"Sleeping for {CHALLENGE_INTERVAL.total_seconds()} seconds before next challenge check...")
                        await asyncio.sleep(CHALLENGE_INTERVAL.total_seconds())
                        continue

                    # If we have enough agents available, start running them in sandboxes and generate eval patches
                    async def run_agent_sandboxes():
                        logger.info("Running sandboxes")
                        sboxes = []
                        
                        # Create and run a sandbox for each agent
                        for agent in agents:
                            try:
                                # Download the agent code from Ridges API
                                logger.info(f"Downloading agent code for agent {agent['agent_id']}")
                                response = await client.get(f"{RIDGES_API_URL}/validator/get/agent-zip", params={"agent_id": agent['agent_id']})
                                response.raise_for_status()
                                logger.info(f"Downloaded agent code for agent {agent['agent_id']}")
                                
                                # Save zip file to temp location
                                tmp_file = tempfile.NamedTemporaryFile(suffix='.zip', delete=False)
                                tmp_file.write(response.content)
                                tmp_file.close()
                                
                                # Unzip the file to a temp directory
                                temp_dir = tempfile.mkdtemp()
                                logger.info(f"Unzipping agent code for agent {agent['agent_id']} to temp directory {temp_dir}")
                                with ZipFile(tmp_file.name, 'r') as zip_ref:
                                    zip_ref.extractall(temp_dir)
                                logger.info(f"Unzipped agent code for agent {agent['agent_id']} to temp directory {temp_dir}")

                                # Create a sandbox for the agent, that runs the agent code from the temp directory
                                sbox = sbox_manager.add_sandbox(src_dir=temp_dir)
                                sbox.run_async(challenge)
                                sboxes.append(sbox)
                            except Exception as e:
                                logger.error(f"Error configuring sandbox for agent {agent['agent_id']}: {e}")
                       
                        # Wait for all sandboxes to finish
                        logger.info("Waiting on sandboxes...")
                        for sbox in sboxes:
                            sbox.wait() # They will be automatically removed from the manager when done

                        # Grab the outputs of each
                        patches = []
                        for sbox in sboxes:
                            if sbox.success:
                                patches.append(sbox.output)
                            else:
                                logger.error(f"Sandbox for agent {agent['agent_id']} failed: {sbox.error}")

                        logger.info(f"Got {len(patches)} patches")

                        # Delete the temporary directories (that hold the agent code)
                        for sbox in sboxes:
                            shutil.rmtree(sbox.src_dir)

                        return patches

                    patches = await run_agent_sandboxes()

                    # Run the eval loop with the patches 
                    logger.info(f"Generated {len(patches)} patches from {len(agents)} agents: task_id={challenge.challenge_id}")

                    # Log background task status
                    logger.info("Background task status:")
                    logger.info(f"  - Weights task running: {not weights_task.done()}")
                    logger.info(f"  - Cleanup task running: {not cleanup_task.done()}")


                    # Evaluate the patches and post scores to database


                    # Sleep until next challenge interval
                    await asyncio.sleep(CHALLENGE_INTERVAL.total_seconds())
                except KeyboardInterrupt:
                    break
                except Exception as e: 
                    logger.error("Error in main loop:")
                    for line in format_exception(type(e), e, e.__traceback__):
                        logger.error(line.rstrip())
                    await asyncio.sleep(CHALLENGE_INTERVAL.total_seconds())
        finally: 
            # Cancel evaluation and weights loops
            weights_task.cancel()
            cleanup_task.cancel()
            try:
                await asyncio.gather(weights_task, cleanup_task, return_exceptions=True)
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
