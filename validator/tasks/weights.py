"""Task for updating weights periodically."""

import asyncio
from validator.tasks.set_weights import set_weights
from validator.config import WEIGHTS_INTERVAL
from shared.logging_utils import get_logger, logging_update_active_coroutines

logger = get_logger(__name__)

async def weights_update_loop() -> None:
    """Run the weights update loop on WEIGHTS_INTERVAL."""
    logger.info("Starting weights update loop")
    consecutive_failures = 0
    max_consecutive_failures = 3

    while True: 
        logging_update_active_coroutines("weights_task", True)
        try: 
            await set_weights()
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