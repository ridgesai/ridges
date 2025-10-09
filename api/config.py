import os
import re
import utils.logger as logger

from dotenv import load_dotenv



# Load everything from .env
load_dotenv()






# Print out the configuration
logger.info("=== Validator Configuration ===")
logger.info(f"Mode: {MODE}")
if MODE == "validator":
    logger.info(f"Validator Wallet Name: {VALIDATOR_WALLET_NAME}")
    logger.info(f"Validator Hotkey Name: {VALIDATOR_HOTKEY_NAME}")
    logger.info(f"Validator Hotkey: {VALIDATOR_HOTKEY.ss58_address}")
elif MODE == "screener":
    logger.info(f"Screener Name: {SCREENER_NAME}")
logger.info(f"Ridges Platform URL: {RIDGES_PLATFORM_URL}")
logger.info(f"Ridges Inference Gateway URL: {RIDGES_INFERENCE_GATEWAY_URL}")
logger.info("===============================")