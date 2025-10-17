import asyncio
import api.config as config
import utils.logger as logger

from api.src.endpoints.validator import delete_validators_that_have_not_sent_a_heartbeat



async def validator_heartbeat_timeout_loop():
    logger.info("Starting validator heartbeat timeout loop...")

    while True:
        logger.info("Deleting validators that have not sent a heartbeat...")

        await delete_validators_that_have_not_sent_a_heartbeat()

        await asyncio.sleep(config.VALIDATOR_HEARTBEAT_TIMEOUT_INTERVAL_SECONDS)

        logger.info("Deleted validators that have not sent a heartbeat")