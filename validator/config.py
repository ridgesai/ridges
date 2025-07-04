from datetime import timedelta
from pathlib import Path
import os
import subprocess

# External package imports
from fiber.chain.chain_utils import load_hotkey_keypair

# Network configuration

# Load validator config from env
NETUID = int(os.getenv("NETUID", "1"))
SUBTENSOR_NETWORK = os.getenv("SUBTENSOR_NETWORK", "test")
SUBTENSOR_ADDRESS = os.getenv("SUBTENSOR_ADDRESS", "ws://127.0.0.1:9945")

# Validator configuration
HOTKEY_NAME = os.getenv("HOTKEY_NAME", "default")
WALLET_NAME = os.getenv("WALLET_NAME", "validator")
MIN_STAKE_THRESHOLD = float(os.getenv("MIN_STAKE_THRESHOLD", "2"))

WEIGHTS_INTERVAL = timedelta(minutes=30)
ALPHA_SCORING_MULTIPLICATOR = 3
NO_RESPONSE_MIN_SCORE = float(os.getenv("NO_RESPONSE_MIN_SCORE", "0.005"))

DB_PATH = Path("validator.db")

VERSION_KEY = 6
VERSION_COMMIT_HASH = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode("utf-8").strip()

# OpenAI Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PREFERRED_OPENAI_MODEL = "gpt-4.1-mini"

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

LOG_DRAIN_FREQUENCY = timedelta(minutes=10)

EASY_INSTANCES = [
    "django__django-11119",
    "django__django-12304",
    "django__django-11880",
    "sympy__sympy-15017",
    "scikit-learn__scikit-learn-14141",
    "pytest-dev__pytest-6202",
    "django__django-14792",
    "pallets__flask-5014",
    "django__django-16333",
    "django__django-11099",
    "astropy__astropy-14309",
    "django__django-11433",
    "django__django-13933",
    "django__django-12209",
    "matplotlib__matplotlib-24570",
    "sympy__sympy-24539",
    "pytest-dev__pytest-7982",
    "django__django-11239",
    "sympy__sympy-15809",
    "scikit-learn__scikit-learn-13135",
    "sympy__sympy-14711",
    "sphinx-doc__sphinx-9320",
    "scikit-learn__scikit-learn-13496",
    "django__django-13297",
    "django__django-15499",
    "scikit-learn__scikit-learn-13439",
    # "astropy__astropy-7166",
    # "sphinx-doc__sphinx-9230",
    # "pytest-dev__pytest-10081",
    # "django__django-10999",
    # "django__django-14534",
    # "django__django-11964",
    # "sympy__sympy-15345",
    # "django__django-14580",
    # "django__django-11179",
    # "sympy__sympy-15875",
    # "django__django-12193",
    # "django__django-15277",
    # "sympy__sympy-19954",
    # "psf__requests-1724",
    # "sphinx-doc__sphinx-9367",
    # "django__django-14493",
    # "django__django-14376",
    # "matplotlib__matplotlib-25479",
    # "django__django-14752",
    # "django__django-13112",
    # "django__django-13406",
    # "sympy__sympy-23534",
    # "sympy__sympy-18189",
    # "matplotlib__matplotlib-24149",
]

MEDIUM_INSTANCES = [
    # "pylint-dev__pylint-8898",
    # "django__django-15957",
]

# Calculate total number of evaluation instances
TOTAL_EVALUATION_INSTANCES = len(EASY_INSTANCES) + len(MEDIUM_INSTANCES)

# Log initial configuration
import logging
logger = logging.getLogger(__name__)
logger.setLevel(LOG_LEVEL)

logger.info("Validator Configuration:")
logger.info(f"Network: {SUBTENSOR_NETWORK}")
logger.info(f"Netuid: {NETUID}")
logger.info(f"Min stake threshold: {MIN_STAKE_THRESHOLD}")
logger.info(f"Weights interval: {WEIGHTS_INTERVAL}")
logger.info(f"DB path: {DB_PATH}")
logger.info(f"Log level: {LOG_LEVEL}")
logger.info(f"Total evaluation instances: {TOTAL_EVALUATION_INSTANCES}")

validator_hotkey = load_hotkey_keypair(WALLET_NAME, HOTKEY_NAME)
