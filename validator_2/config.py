import os
import utils.logger as logger

from dotenv import load_dotenv



# Load everything from .env
load_dotenv()

VALIDATOR_HOTKEY = os.getenv("VALIDATOR_HOTKEY")
if not VALIDATOR_HOTKEY:
    print("VALIDATOR_HOTKEY is not set in .env")
    exit(1)

RIDGES_PLATFORM_URL = os.getenv("RIDGES_PLATFORM_URL")
if not RIDGES_PLATFORM_URL:
    print("RIDGES_PLATFORM_URL is not set in .env")
    exit(1)

RIDGES_INFERENCE_GATEWAY_URL = os.getenv("RIDGES_INFERENCE_GATEWAY_URL")
if not RIDGES_INFERENCE_GATEWAY_URL:
    print("RIDGES_INFERENCE_GATEWAY_URL is not set in .env")
    exit(1)



# Print out the configuration
logger.banner('VALIDATOR CONFIGURATION')
logger.info(f"Validator Hotkey: {VALIDATOR_HOTKEY}")
logger.info(f"Ridges Platform URL: {RIDGES_PLATFORM_URL}")
logger.info(f"Ridges Inference Gateway URL: {RIDGES_INFERENCE_GATEWAY_URL}")
logger.banner()