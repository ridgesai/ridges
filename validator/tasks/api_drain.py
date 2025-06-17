"""Task for draining data to Ridges API periodically."""

import time
import asyncio
import httpx
from validator.db.operations import DatabaseManager
from validator.config import (
    WEIGHTS_INTERVAL, LOG_DRAIN_FREQUENCY,
    RIDGES_API_URL, VERSION_COMMIT_HASH
)
from shared.logging_utils import get_logger, logging_update_active_coroutines

logger = get_logger(__name__)

async def post_to_ridges_api(db_manager: DatabaseManager, validator_hotkey: str):
    """Periodically post data to Ridges API."""
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