import asyncio

import api.config as config
import utils.logger as logger
from api.endpoints.validator import (
    delete_validators_that_have_not_sent_a_heartbeat,
)


async def validator_heartbeat_timeout_loop():
    logger.info("Starting validator heartbeat timeout loop...")

    # TODO THIS CHANGES THE EXISTING BEHAVIOUR, DO WE WANT TO DO THIS ?
    while True:
        try:
            await delete_validators_that_have_not_sent_a_heartbeat()
        except Exception as e:
            logger.error(f"Unexpected error in heartbeat timeout loop: {type(e).__name__}: {e}")

        await asyncio.sleep(config.VALIDATOR_HEARTBEAT_TIMEOUT_INTERVAL_SECONDS)
