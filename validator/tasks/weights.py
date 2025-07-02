"""Task for updating weights periodically."""

import asyncio
from validator.tasks.set_weights import set_weights
from validator.config import WEIGHTS_INTERVAL
from validator.utils.logging import get_logger, logging_update_active_coroutines

logger = get_logger(__name__)

async def weights_update_loop() -> None:
    """Run the weights update loop on WEIGHTS_INTERVAL."""
    # This loop is now disabled because weight setting is triggered explicitly
    # by the "set-weights" websocket event, which provides the winning miner's
    # hotkey.  We keep the coroutine alive (sleeping) so any legacy startup code
    # that awaits it will still work, but it no longer performs periodic
    # updates.

    logger.info("Periodic weights update loop disabled – waiting indefinitely for explicit set-weights events")

    while True:
        await asyncio.sleep(24 * 60 * 60)  # sleep one day; practically infinite 