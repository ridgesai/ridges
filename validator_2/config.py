import os
import logging
import utils.logger as logger

from dotenv import load_dotenv

from fiber.chain.chain_utils import load_hotkey_keypair
logging.getLogger('chain_utils').setLevel(logging.WARNING)



# Load everything from .env
load_dotenv()



# Load wallet name and hotkey name
VALIDATOR_WALLET_NAME = os.getenv("VALIDATOR_WALLET_NAME")
if not VALIDATOR_WALLET_NAME:
    logger.fatal("VALIDATOR_WALLET_NAME is not set in .env")

VALIDATOR_HOTKEY_NAME = os.getenv("VALIDATOR_HOTKEY_NAME")
if not VALIDATOR_HOTKEY_NAME:
    logger.fatal("VALIDATOR_HOTKEY_NAME is not set in .env")

# Load the hotkey
try:
    VALIDATOR_HOTKEY = load_hotkey_keypair(VALIDATOR_WALLET_NAME, VALIDATOR_HOTKEY_NAME)
except Exception as e:
    logger.fatal(f"Error loading hotkey: {e}")



# Load URLs (and strip trailing slashes)
RIDGES_PLATFORM_URL = os.getenv("RIDGES_PLATFORM_URL")
if not RIDGES_PLATFORM_URL:
    logger.fatal("RIDGES_PLATFORM_URL is not set in .env")

RIDGES_PLATFORM_URL = RIDGES_PLATFORM_URL.rstrip("/")

RIDGES_INFERENCE_GATEWAY_URL = os.getenv("RIDGES_INFERENCE_GATEWAY_URL")
if not RIDGES_INFERENCE_GATEWAY_URL:
    logger.fatal("RIDGES_INFERENCE_GATEWAY_URL is not set in .env")

RIDGES_INFERENCE_GATEWAY_URL = RIDGES_INFERENCE_GATEWAY_URL.rstrip("/")



# Print out the configuration
logger.banner('VALIDATOR CONFIGURATION')
logger.info(f"Validator Wallet Name: {VALIDATOR_WALLET_NAME}")
logger.info(f"Validator Hotkey Name: {VALIDATOR_HOTKEY_NAME}")
logger.info(f"Validator Hotkey: {VALIDATOR_HOTKEY.ss58_address}")
logger.info(f"Ridges Platform URL: {RIDGES_PLATFORM_URL}")
logger.info(f"Ridges Inference Gateway URL: {RIDGES_INFERENCE_GATEWAY_URL}")
logger.banner()