import asyncio
import logging

import api.config as config
from api.endpoints.validator import (
    delete_validators_that_have_not_sent_a_heartbeat,
)

logger = logging.getLogger(__name__)


async def validator_heartbeat_timeout_loop():
    logger.info("Starting validator heartbeat timeout loop...")

    while True:
        await delete_validators_that_have_not_sent_a_heartbeat()

        await asyncio.sleep(config.VALIDATOR_HEARTBEAT_TIMEOUT_INTERVAL_SECONDS)
