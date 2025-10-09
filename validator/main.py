import time
import asyncio
import pathlib
import traceback
import utils.logger as logger
import validator.config as config

from validator.http import post_ridges_platform
from evaluator.sandbox.sandbox_manager import SandboxManager
from evaluator.problem_suites.polyglot.polyglot_suite import PolyglotSuite
from evaluator.problem_suites.swebench_verified.swebench_verified_suite import SWEBenchVerifiedSuite



# The session ID for this validator
session_id = None



async def disconnect(reason: str):
    if session_id is None:
        return
    
    try:
        logger.info("Disconnecting validator...")
        await post_ridges_platform("/validator/disconnect", {"reason": reason}, bearer_token=session_id)
        logger.info("Disconnected validator")
    except Exception as e:
        logger.error(f"Error disconnecting validator: {type(e).__name__}: {e}")



async def main():
    global session_id



    # Register with the Ridges platform, yielding us a session ID
    logger.info("Registering validator...")

    if config.MODE == "validator":
        # Get the current timestamp, and sign it with the validator hotkey
        timestamp = int(time.time())
        signed_timestamp = config.VALIDATOR_HOTKEY.sign(str(timestamp)).hex()
        
        register_response = await post_ridges_platform("/validator/register-as-validator", {
            "timestamp": timestamp,
            "signed_timestamp": signed_timestamp,
            "hotkey": config.VALIDATOR_HOTKEY.ss58_address
        })
    
    elif config.MODE == "screener":
        register_response = await post_ridges_platform("/validator/register-as-screener", {
            "name": config.SCREENER_NAME,
            "password": config.SCREENER_PASSWORD
        })
    
    session_id = register_response["session_id"]

    logger.info(f"Registered validator. Session ID: {session_id}")



    # Create the sandbox manager
    sandbox_manager = SandboxManager(config.RIDGES_INFERENCE_GATEWAY_URL)

    # Load all problem suites
    datasets_path = pathlib.Path(__file__).parent.parent / "evaluator" / "datasets"
    polyglot_suite = PolyglotSuite(datasets_path / "polyglot")
    swebench_verified_suite = SWEBenchVerifiedSuite(datasets_path / "swebench_verified")



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



if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.warning("Keyboard interrupt")
        asyncio.run(disconnect("Keyboard interrupt"))
    except Exception as e:
        logger.error(f"Error in main(): {type(e).__name__}: {e}")
        logger.error(traceback.format_exc())
        asyncio.run(disconnect("Error in main()"))