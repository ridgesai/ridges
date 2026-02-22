import asyncio
import api.config as config
import utils.logger as logger

from utils.bittensor import fetch_and_save_registered_hotkeys



async def fetch_metagraph_loop():
    logger.info("Starting fetch metagraph loop...")

    while True:
        await fetch_and_save_registered_hotkeys()

        await asyncio.sleep(config.FETCH_METAGRAPH_INTERVAL_SECONDS)