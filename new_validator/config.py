from datetime import timedelta
import os
import subprocess
from pathlib import Path
from dotenv import load_dotenv

env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)

# External package imports
from fiber.chain.chain_utils import load_hotkey_keypair

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
    print("RIDGES_API_URL must be set in new_validator/.env")
    exit(1)
if RIDGES_API_URL == "http://<YOUR_LOCAL_IP>:8000":
    print("Set your local IP address in new_validator/.env")
    exit(1)
if RIDGES_API_URL in ["http://127.0.0.1:8000", "http://localhost:8000", "http://0.0.0.0:8000"]:
    print("You are running the validator on a loopback address. This will cause 502 connection errors while proxying. Please use your local IP address.")
    exit(1)
RIDGES_PROXY_URL = os.getenv("RIDGES_PROXY_URL", "http://52.1.119.189:8001")

LOG_DRAIN_FREQUENCY = timedelta(minutes=10)

WEBSOCKET_URL = RIDGES_API_URL.replace("http", "ws", 1) + "/ws" if RIDGES_API_URL else None

# Log initial configuration
from utils.logging_utils import get_logger
logger = get_logger(__name__)

logger.info("Validator Configuration:")
logger.info(f"Network: {SUBTENSOR_NETWORK}")
logger.info(f"Netuid: {NETUID}")
logger.info(f"Min stake threshold: {MIN_STAKE_THRESHOLD}")
logger.info(f"Log level: {LOG_LEVEL}")

validator_hotkey = None
screener_hotkey = None





# Sandbox constants
AGENT_TIMEOUT = 40 * 60
EVAL_TIMEOUT = 10 * 60