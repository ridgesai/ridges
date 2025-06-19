from datetime import timedelta
from pathlib import Path
import os

# Network configuration
# Consider defaulting to testnet uid?
NETUID = int(os.getenv("NETUID", "62"))
SUBTENSOR_NETWORK = os.getenv("SUBTENSOR_NETWORK", "finney")
SUBTENSOR_ADDRESS = os.getenv("SUBTENSOR_ADDRESS", "ws://127.0.0.1:9945")

# Validator configuration
HOTKEY_NAME = os.getenv("HOTKEY_NAME", "default")
WALLET_NAME = os.getenv("WALLET_NAME", "validator")
MIN_STAKE_THRESHOLD = float(os.getenv("MIN_STAKE_THRESHOLD", "2"))
VALIDATOR_PORT = int(os.getenv("VALIDATOR_PORT", "8000"))
VALIDATOR_HOST = os.getenv("VALIDATOR_HOST", "0.0.0.0")

# Default configuration values
MIN_MINERS = 1
MAX_MINERS = 25

# Additional settings needed for operation
CHALLENGE_INTERVAL = timedelta(minutes=1)
CHALLENGE_TIMEOUT = timedelta(minutes=4)

# Codegen task configuration
MIN_FILES_IN_DIR_TO_GENERATE_PROBLEM = 3
MIN_FILE_CONTENT_LEN_CHARS = 50

WEIGHTS_INTERVAL = timedelta(minutes=30)
ALPHA_SCORING_MULTIPLICATOR = 3
MOCK_RESPONSES = False

DB_PATH = Path("validator.db")

VERSION_KEY = 2

# OpenAI Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PREFERRED_OPENAI_MODEL = "gpt-4.1-mini"

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG")
RIDGES_API_URL = "http://54.159.224.114/"
LOG_DRAIN_FREQUENCY = timedelta(minutes=3)

# Log initial configuration
import logging
logger = logging.getLogger(__name__)
logger.setLevel(LOG_LEVEL)

logger.info("Validator Configuration:")
logger.info(f"Network: {SUBTENSOR_NETWORK}")
logger.info(f"Netuid: {NETUID}")
logger.info(f"Min miners: {MIN_MINERS}")
logger.info(f"Max miners: {MAX_MINERS}")
logger.info(f"Min stake threshold: {MIN_STAKE_THRESHOLD}")
logger.info(f"Challenge interval: {CHALLENGE_INTERVAL}")
logger.info(f"Challenge timeout: {CHALLENGE_TIMEOUT}")
logger.info(f"Weights interval: {WEIGHTS_INTERVAL}")
logger.info(f"DB path: {DB_PATH}")
logger.info(f"Log level: {LOG_LEVEL}")