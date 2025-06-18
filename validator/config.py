from datetime import timedelta
from pathlib import Path
import os
import subprocess

# Network configuration

# Load validator config from env
NETUID = int(os.getenv("NETUID", "1"))
SUBTENSOR_NETWORK = os.getenv("SUBTENSOR_NETWORK", "test")
SUBTENSOR_ADDRESS = os.getenv("SUBTENSOR_ADDRESS", "ws://127.0.0.1:9945")

# Validator configuration
HOTKEY_NAME = os.getenv("HOTKEY_NAME", "default")
WALLET_NAME = os.getenv("WALLET_NAME", "validator")
MIN_STAKE_THRESHOLD = float(os.getenv("MIN_STAKE_THRESHOLD", "2"))

# Default configuration values
MIN_MINERS = 1
MAX_MINERS = 100

# Additional settings needed for operation
CHALLENGE_INTERVAL = timedelta(minutes=10)
CHALLENGE_TIMEOUT = timedelta(minutes=10)

# Codegen task configuration
MIN_FILES_IN_DIR_TO_GENERATE_PROBLEM = 3
MIN_FILE_CONTENT_LEN_CHARS = 50

WEIGHTS_INTERVAL = timedelta(minutes=30)
ALPHA_SCORING_MULTIPLICATOR = 3
MOCK_RESPONSES = False
NO_RESPONSE_MIN_SCORE = float(os.getenv("NO_RESPONSE_MIN_SCORE", "0.005"))

DB_PATH = Path("validator.db")

VERSION_KEY = 2
VERSION_COMMIT_HASH = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode("utf-8").strip()

# OpenAI Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PREFERRED_OPENAI_MODEL = "gpt-4.1-mini"

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG")
RIDGES_API_URL = os.getenv("RIDGES_API_URL", "https://api.ridges.ai")
LOG_DRAIN_FREQUENCY = timedelta(minutes=10)

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