import httpx
import config
import asyncio
import utils.logger as logger

from utils.http import post_ridges_platform



async def main():
    logger.enable_verbose()

    try:
        # Register with the Ridges platform
        logger.info("Registering validator...")
        register_response = await post_ridges_platform('/validator/register', {
            "signed_timestamp": "FOOBAR",
            "hotkey": config.VALIDATOR_HOTKEY
        })
        session_id = register_response.json()["session_id"]
        logger.info(f"Registered validator. Session ID: {session_id}")
    
    except Exception as e:
        logger.error(f"Error in main(): {type(e).__name__}: {e}")
        exit(1)
        



if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.warn("Keyboard interrupt")