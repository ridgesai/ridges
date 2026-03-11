import asyncio
import api.config as config
import utils.logger as logger

from api.endpoints.validator import detect_and_handle_stalled_evaluations



async def validator_stalled_evaluation_loop():
    logger.info("Starting validator stalled evaluation detection loop...")

    while True:
        await detect_and_handle_stalled_evaluations()

        await asyncio.sleep(config.VALIDATOR_STALLED_EVALUATION_CHECK_INTERVAL_SECONDS)
