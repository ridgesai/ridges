import os
import re
import logging
import utils.logger as logger

from dotenv import load_dotenv
from fiber.chain.chain_utils import load_hotkey_keypair



# Load everything from .env
load_dotenv()



# Load the mode
MODE = os.getenv("MODE")
if not MODE:
    logger.fatal("MODE is not set in .env")

if MODE != "screener" and MODE != "validator":
    logger.fatal("MODE must be either 'screener' or 'validator'")



if MODE == "validator":
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



elif MODE == "screener":
    # Load the screener name
    SCREENER_NAME = os.getenv("SCREENER_NAME")
    if not SCREENER_NAME:
        logger.fatal("SCREENER_NAME is not set in .env")

    # Ensure that the screener name is in the format screener-CLASS-NUM
    if not re.match(r"screener-\d-\d+", SCREENER_NAME):
        logger.fatal("SCREENER_NAME must be in the format screener-CLASS-NUM")

    # Ensure that CLASS is either 1 or 2
    screener_class = SCREENER_NAME.split("-")[1]
    if screener_class != "1" and screener_class != "2":
        logger.fatal("SCREENER_NAME must be in the format screener-CLASS-NUM where CLASS is 1 or 2")

    # Load the screener password
    SCREENER_PASSWORD = os.getenv("SCREENER_PASSWORD")
    if not SCREENER_PASSWORD:
        logger.fatal("SCREENER_PASSWORD is not set in .env")



# Load URLs (and strip trailing slashes)
RIDGES_PLATFORM_URL = os.getenv("RIDGES_PLATFORM_URL")
if not RIDGES_PLATFORM_URL:
    logger.fatal("RIDGES_PLATFORM_URL is not set in .env")

RIDGES_PLATFORM_URL = RIDGES_PLATFORM_URL.rstrip("/")

RIDGES_INFERENCE_GATEWAY_URL = os.getenv("RIDGES_INFERENCE_GATEWAY_URL")
if not RIDGES_INFERENCE_GATEWAY_URL:
    logger.fatal("RIDGES_INFERENCE_GATEWAY_URL is not set in .env")

RIDGES_INFERENCE_GATEWAY_URL = RIDGES_INFERENCE_GATEWAY_URL.rstrip("/")



# Load the time to wait between requesting for a new evaluation
REQUEST_EVALUATION_INTERVAL_SECONDS = int(os.getenv("REQUEST_EVALUATION_INTERVAL_SECONDS"))
if not REQUEST_EVALUATION_INTERVAL_SECONDS:
    logger.fatal("REQUEST_EVALUATION_INTERVAL_SECONDS is not set in .env")



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