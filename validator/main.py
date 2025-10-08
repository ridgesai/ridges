import time
import asyncio
import utils.logger as logger
import validator.config as config

from validator.http import post_ridges_platform



# The session ID for this validator
session_id = None



async def disconnect():
    if session_id is None:
        return
    
    try:
        logger.info("Disconnecting validator...")
        await post_ridges_platform("/validator/disconnect", {"reason": "Keyboard interrupt"}, bearer_token=session_id)
        logger.info("Disconnected validator")
    except Exception as e:
        logger.error(f"Error disconnecting validator: {type(e).__name__}: {e}")



async def main():
    global session_id



    try:
        # Get the current timestamp, and sign it with the validator hotkey
        timestamp = int(time.time())
        signed_timestamp = config.VALIDATOR_HOTKEY.sign(str(timestamp)).hex()


        
        # Register with the Ridges platform, yielding us a session ID
        logger.info("Registering validator...")
        register_response = await post_ridges_platform("/validator/register", {
            "timestamp": timestamp,
            "signed_timestamp": signed_timestamp,
            "hotkey": config.VALIDATOR_HOTKEY.ss58_address
        })
        session_id = register_response["session_id"]
        logger.info(f"Registered validator. Session ID: {session_id}")



        # Request an evaluation
        while True:
            logger.info("Requesting an evaluation...")
            
            evaluation_response = await post_ridges_platform("/validator/request-evaluation", bearer_token=session_id)


            # If no evaluation is available, wait and try again
            if evaluation_response is None:
                logger.info(f"No evaluations available. Waiting for {config.REQUEST_EVALUATION_INTERVAL_SECONDS} seconds...")
                await asyncio.sleep(config.REQUEST_EVALUATION_INTERVAL_SECONDS)
                continue

            logger.info("Received evaluation:")
            logger.info(f"  # of lines in agent code: {len(evaluation_response['agent_code'].splitlines())}")
            logger.info(f"  # of evaluation runs: {len(evaluation_response['evaluation_runs'])}")



    except Exception as e:
        logger.error(f"Error in main(): {type(e).__name__}: {e}")
        exit(1)



if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.warning("Keyboard interrupt")
        asyncio.run(disconnect())