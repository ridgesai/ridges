import asyncio
import logging

import api.config as config
from utils.bittensor import subtensor_client

logger = logging.getLogger(__name__)


async def subtensor_keepalive_loop():
    """Keep the subtensor websocket alive and rebuild it when it stops responding.

    Serves two purposes:

    1. The periodic chain read is traffic, which stops the socket from closing
    2. Rebuilding on a failed ping recovers the wedged state the substrate library can reach after exhausting its internal reconnect attempts, which otherwise persists until the process restarts.
    """
    logger.info("Starting subtensor keepalive loop...")

    while True:
        try:
            if not await subtensor_client.ping():
                logger.warning("Subtensor ping failed; rebuilding connection")
                await subtensor_client.reconnect()
                logger.info("Subtensor connection rebuilt")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Subtensor keepalive iteration failed")

        await asyncio.sleep(config.SUBTENSOR_KEEPALIVE_INTERVAL_SECONDS)
