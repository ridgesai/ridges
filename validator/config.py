from datetime import timedelta
import os
import subprocess
import platform

# External package imports
from fiber.chain.chain_utils import load_hotkey_keypair

# Set Docker platform for Apple Silicon compatibility
if platform.machine() == 'arm64' and not os.getenv('DOCKER_DEFAULT_PLATFORM'):
    os.environ['DOCKER_DEFAULT_PLATFORM'] = 'linux/arm64'

SCREENER_MODE = os.getenv("SCREENER_MODE", "false") == "true"

# Load validator config from env
NETUID = int(os.getenv("NETUID", "1"))
SUBTENSOR_NETWORK = os.getenv("SUBTENSOR_NETWORK", "test")
SUBTENSOR_ADDRESS = os.getenv("SUBTENSOR_ADDRESS", "ws://127.0.0.1:9945")

# Validator configuration
HOTKEY_NAME = os.getenv("HOTKEY_NAME", "default")
WALLET_NAME = os.getenv("WALLET_NAME", "validator")
MIN_STAKE_THRESHOLD = float(os.getenv("MIN_STAKE_THRESHOLD", "2"))

VERSION_KEY = 6
VERSION_COMMIT_HASH = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode("utf-8").strip()

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG")
RIDGES_API_URL = os.getenv("RIDGES_API_URL", None) 
if RIDGES_API_URL is None:
    print("RIDGES_API_URL must be set in validator/.env")
    exit(1)
if RIDGES_API_URL == "http://<YOUR_LOCAL_IP>:8000":
    print("Set your local IP address in validator/.env")
    exit(1)
if RIDGES_API_URL in ["http://127.0.0.1:8000", "http://localhost:8000", "http://0.0.0.0:8000"]:
    print("You are running the validator on a loopback address. This will cause 502 connection errors while proxying. Please use your local IP address.")
    exit(1)
RIDGES_PROXY_URL = os.getenv("RIDGES_PROXY_URL", "http://52.1.119.189:8001")

LOG_DRAIN_FREQUENCY = timedelta(minutes=10)

# Log initial configuration
from loggers.logging_utils import get_logger
logger = get_logger(__name__)

logger.info("Validator Configuration:")
logger.info(f"Network: {SUBTENSOR_NETWORK}")
logger.info(f"Netuid: {NETUID}")
logger.info(f"Min stake threshold: {MIN_STAKE_THRESHOLD}")
logger.info(f"Log level: {LOG_LEVEL}")

validator_hotkey = None
screener_hotkey = None
if SCREENER_MODE:
    screener_hotkey = os.getenv("SCREENER_HOTKEY")
    # Check for screener password
    screener_password = os.getenv("SCREENER_PASSWORD")
    if not screener_password:
        logger.warning("WARNING: Screener mode is enabled but SCREENER_PASSWORD is not set!")
        logger.warning("WARNING: The screener will not be able to authenticate with the proxy for inference/embedding requests!")
        logger.warning("WARNING: Please set SCREENER_PASSWORD environment variable.")
else:
    validator_hotkey = load_hotkey_keypair(WALLET_NAME, HOTKEY_NAME)

AGENT_TIMEOUT = int(os.getenv("AGENT_TIMEOUT", "2400"))
EVAL_TIMEOUT = int(os.getenv("EVAL_TIMEOUT", "600"))