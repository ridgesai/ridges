import asyncio
import logging

import api.config as config
from api.endpoints.validator import (
    delete_validators_that_have_not_sent_a_heartbeat,
)

logger = logging.getLogger(__name__)


async def validator_heartbeat_timeout_loop():
    logger.info("Validator heartbeat timeout loop started")

    # TODO THIS CHANGES THE EXISTING BEHAVIOUR, DO WE WANT TO DO THIS ?
    while True:
        try:
            await delete_validators_that_have_not_sent_a_heartbeat()
        except Exception as e:
            logger.error("Heartbeat timeout loop error", extra={"error": f"{type(e).__name__}: {e}"})

        await asyncio.sleep(config.VALIDATOR_HEARTBEAT_TIMEOUT_INTERVAL_SECONDS)
