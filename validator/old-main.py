


# Python built in imports 
from pathlib import Path
import sys
import uuid

from validator.db.schema import AgentVersion, init_db

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
from shared.logging_utils import get_logger
from validator.config import (
    RIDGES_API_URL, WALLET_NAME, HOTKEY_NAME, CHALLENGE_INTERVAL,
    CHALLENGE_TIMEOUT, DB_PATH
)
# from validator.tasks.weights import weights_update_loop
# from validator.tasks.api_drain import post_to_ridges_api
from validator.tasks.evaluate_agent_version import evaluate_agent_version

# Set up logger
logger = get_logger(__name__)
logger.info(f"Loaded environment variables from {env_path.absolute()}")



async def get_next_agent_version() -> AgentVersion:
    """Get the next agent version to run tests on"""

    async with httpx.AsyncClient(timeout=CHALLENGE_TIMEOUT.total_seconds()) as client:
        logger.info(f"{RIDGES_API_URL}/retrieval/agent-list")
        response = await client.get(f"{RIDGES_API_URL}/retrieval/agent-list", params={"type": "codegen"})
        response.raise_for_status()
        agents = response.json()["details"]["agents"]
        agent = agents[0]
        return AgentVersion(
            agent_id=agent["agent_id"],
            version_id=str(uuid.uuid4()),
            version_number=agent["version"],
            created_at=agent["created_at"],
        )

async def main():
    """Main validator loop."""

    # Load validator hotkey
    try:
        hotkey = load_hotkey_keypair(WALLET_NAME, HOTKEY_NAME)
    except Exception as e:
        logger.error(f"Failed to load keys: {str(e)}")
        return
    
    init_db()
    
    # Initialize HTTP client with long timeout
    async with httpx.AsyncClient(timeout=CHALLENGE_TIMEOUT.total_seconds()) as client:
        active_challenge_tasks = []  # Track active challenges

        # # Start weights update loop as a separate task
        # logger.info("Starting weights update task...")
        # weights_task = asyncio.create_task(weights_update_loop(db_manager))
        # weights_task.add_done_callback(
        #     lambda t: logger.error(f"Weights task ended unexpectedly: {t.exception()}")
        #     if t.exception() else None
        # )

        # # Start a task to periodically push non sensitive data to Ridges for the dashboard
        # logger.info("Starting Ridges API task...")
        # api_drain_task = asyncio.create_task(post_to_ridges_api(db_manager, validator_hotkey=hotkey.ss58_address))
        # api_drain_task.add_done_callback(
        #     lambda t: logger.error(f"Ridges API task ended unexpectedly: {t.exception()}")
        #     if t.exception() else None
        # )

        # runs the main iteration loop within async client
        try:
            # Main challenge loop
            iteration = 0 
            while True: 
                try:
                    iteration += 1

                    logger.info(f"Main loop iteration {iteration}")

                    # # Check if any background tasks failed
                    # for task in [weights_task, api_drain_task]:
                    #     if task.done() and not task.cancelled():
                    #         exc = task.exception()
                    #         if exc:
                    #             logger.error(f"Background task failed: {exc}")
                    #             # Restart the failed task
                    #             if task == weights_task:
                    #                 logger.info("Restarting weights update loop...")
                    #                 weights_task = asyncio.create_task(weights_update_loop(db_manager))
                    #             elif task == api_drain_task:
                    #                 logger.info("Restarting API drain task...")
                    #                 api_drain_task = asyncio.create_task(post_to_ridges_api(db_manager, validator_hotkey=hotkey.ss58_address))
                    
                    # # Clean up completed challenge tasks
                    # active_challenge_tasks = [
                    #     task for task in active_challenge_tasks 
                    #     if not task.task.done()
                    # ]

                    # Get the next agent version
                    agent_version = await get_next_agent_version()

                    logger.info(agent_version)

                    await evaluate_agent_version(agent_version)

                    # # Log background task status
                    # logger.info("Background task status:")
                    # logger.info(f"  - Weights task running: {not weights_task.done()}")
                    # logger.info(f"  - API drain task running: {not api_drain_task.done()}")

                    # # Log status
                    # num_active_challenges = len(active_challenge_tasks)
                    # if num_active_challenges > 0:
                    #     logger.info(f"Currently tracking {num_active_challenges} active challenges")

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
            # # Cancel weights loops
            # weights_task.cancel()
            # api_drain_task.cancel()
            # try:
            #     await asyncio.gather(weights_task, api_drain_task, return_exceptions=True)
            # except asyncio.CancelledError:
                pass
        
# Lastly the if name runs it all and then exists out on keyboard interrupt
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
