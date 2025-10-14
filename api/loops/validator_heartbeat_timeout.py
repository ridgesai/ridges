import asyncio
import api.config as config
import utils.logger as logger

from api.src.endpoints.validator import delete_validators_that_have_not_sent_a_heartbeat



async def validator_heartbeat_timeout_loop():
    logger.info("Starting validator heartbeat timeout loop...")

    while True:
        await delete_validators_that_have_not_sent_a_heartbeat()

        await asyncio.sleep(config.VALIDATOR_HEARTBEAT_TIMEOUT_LOOP_INTERVAL_SECONDS)