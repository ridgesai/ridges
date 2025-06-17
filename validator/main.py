# Python built in imports 
from pathlib import Path
import sys 

# Add project root to Python path before other imports
project_root = str(Path(__file__).resolve().parents[2])
sys.path.append(project_root)

import os
import asyncio

# External package imports
from fiber.chain.chain_utils import load_hotkey_keypair
from dotenv import load_dotenv
import httpx
from openai import OpenAI
from traceback import format_exception

# Load environment variables
validator_dir = Path(__file__).parent
env_path = validator_dir / ".env"
load_dotenv(env_path)

# Internal package imports
from validator.db.operations import DatabaseManager
from validator.challenge.create_codegen_challenge import create_next_codegen_challenge
from shared.logging_utils import get_logger
from validator.config import (
    WALLET_NAME, HOTKEY_NAME, CHALLENGE_INTERVAL,
    CHALLENGE_TIMEOUT, DB_PATH
)
from validator.evaluation.evaluation_loop import run_evaluation_loop
from validator.tasks.weights import weights_update_loop
from validator.tasks.api_drain import post_to_ridges_api
from validator.tasks.run_agents import run_agent_sandboxes

# Set up logger
logger = get_logger(__name__)
logger.info(f"Loading environment variables from {env_path.absolute()}")

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
        weights_task = asyncio.create_task(weights_update_loop(db_manager))
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
                                if task == weights_task:
                                    logger.info("Restarting weights update loop...")
                                    weights_task = asyncio.create_task(weights_update_loop(db_manager))
                                elif task == api_drain_task:
                                    logger.info("Restarting API drain task...")
                                    api_drain_task = asyncio.create_task(post_to_ridges_api(db_manager, validator_hotkey=hotkey.ss58_address))
                    
                    # Clean up completed challenge tasks
                    active_challenge_tasks = [
                        task for task in active_challenge_tasks 
                        if not task.task.done()
                    ]

                    # Fetch next challenge from API with retries
                    challenge = await create_next_codegen_challenge(hotkey.ss58_address, openai_client)

                    # If there is an error generating the challenge, wait for a bit and rerun the loop
                    if not challenge:
                        logger.info(f"Sleeping for {CHALLENGE_INTERVAL.total_seconds()} seconds before next challenge check...")
                        await asyncio.sleep(CHALLENGE_INTERVAL.total_seconds())
                        continue

                    challenge.store_in_database(db_manager)
                    db_manager.mark_challenge_sent(challenge.challenge_id, hotkey.ss58_address)

                    # Run agents in sandboxes and collect patches
                    await run_agent_sandboxes(challenge)

                    # Log background task status
                    logger.info("Background task status:")
                    logger.info(f"  - Evaluation task running: {not evaluation_task.done()}")
                    logger.info(f"  - Weights task running: {not weights_task.done()}")
                    logger.info(f"  - API drain task running: {not api_drain_task.done()}")

                    # Log status
                    num_active_challenges = len(active_challenge_tasks)
                    if num_active_challenges > 0:
                        logger.info(f"Currently tracking {num_active_challenges} active challenges")

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
