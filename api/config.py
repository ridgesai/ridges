import os

from dotenv import load_dotenv

import utils.logger as logger

# Load everything from .env
load_dotenv()


# Load host and port
HOST = os.getenv("HOST")
if not HOST:
    logger.fatal("HOST is not set in .env")

PORT = os.getenv("PORT")
if not PORT:
    logger.fatal("PORT is not set in .env")
PORT = int(PORT)


# Load Bittensor configuration
NETUID = os.getenv("NETUID")
if not NETUID:
    logger.fatal("NETUID is not set in .env")
NETUID = int(NETUID)

SUBTENSOR_ADDRESS = os.getenv("SUBTENSOR_ADDRESS")
if not SUBTENSOR_ADDRESS:
    logger.fatal("SUBTENSOR_ADDRESS is not set in .env")

SUBTENSOR_NETWORK = os.getenv("SUBTENSOR_NETWORK")
if not SUBTENSOR_NETWORK:
    logger.fatal("SUBTENSOR_NETWORK is not set in .env")


OWNER_HOTKEY = os.getenv("OWNER_HOTKEY")
if not OWNER_HOTKEY:
    logger.fatal("OWNER_HOTKEY is not set in .env")

UPLOAD_SEND_ADDRESS = os.getenv("UPLOAD_SEND_ADDRESS")
if not UPLOAD_SEND_ADDRESS:
    logger.fatal("UPLOAD_SEND_ADDRESS is not set in .env")

BURN = os.getenv("BURN")
if not BURN:
    logger.fatal("BURN is not set in .env")
BURN = BURN.lower() == "true"

DISALLOW_UPLOADS = os.getenv("DISALLOW_UPLOADS")
if not DISALLOW_UPLOADS:
    logger.fatal("DISALLOW_UPLOADS is not set in .env")
DISALLOW_UPLOADS = DISALLOW_UPLOADS.lower() == "true"

if DISALLOW_UPLOADS:
    DISALLOW_UPLOADS_REASON = os.getenv("DISALLOW_UPLOADS_REASON")
    if not DISALLOW_UPLOADS_REASON:
        logger.fatal("DISALLOW_UPLOADS_REASON is not set in .env")


# Load the environment configuration
ENV = os.getenv("ENV")
if not ENV:
    logger.fatal("ENV is not set in .env")

if ENV != "prod" and ENV != "dev":
    logger.fatal("ENV must be either 'prod' or 'dev'")


# Load AWS configuration
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
if not AWS_ACCESS_KEY_ID:
    logger.fatal("AWS_ACCESS_KEY_ID is not set in .env")

AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
if not AWS_SECRET_ACCESS_KEY:
    logger.fatal("AWS_SECRET_ACCESS_KEY is not set in .env")

AWS_REGION = os.getenv("AWS_REGION")
if not AWS_REGION:
    logger.fatal("AWS_REGION is not set in .env")

RIDGES_AGENT_KEY_ENCRYPTION_KEY = os.getenv("RIDGES_AGENT_KEY_ENCRYPTION_KEY")
if not RIDGES_AGENT_KEY_ENCRYPTION_KEY:
    logger.fatal(
        "RIDGES_AGENT_KEY_ENCRYPTION_KEY is not set in .env; miner OpenRouter secret encryption/decryption "
        "will be unavailable until it is configured."
    )


# Load S3 configuration
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
if not S3_BUCKET_NAME:
    logger.fatal("S3_BUCKET_NAME is not set in .env")

# Load S3 endpoint URL (optional, used for testing with local S3 emulators or connecting to S3-compatible services)
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL")
if not S3_ENDPOINT_URL:
    logger.debug("S3_ENDPOINT_URL is not set in .env")


# Load database configuration
DATABASE_USERNAME = os.getenv("DATABASE_USERNAME")
if not DATABASE_USERNAME:
    logger.fatal("DATABASE_USERNAME is not set in .env")

DATABASE_PASSWORD = os.getenv("DATABASE_PASSWORD")
if not DATABASE_PASSWORD:
    logger.fatal("DATABASE_PASSWORD is not set in .env")

DATABASE_HOST = os.getenv("DATABASE_HOST")
if not DATABASE_HOST:
    logger.fatal("DATABASE_HOST is not set in .env")

DATABASE_PORT = os.getenv("DATABASE_PORT")
if not DATABASE_PORT:
    logger.fatal("DATABASE_PORT is not set in .env")
DATABASE_PORT = int(DATABASE_PORT)

DATABASE_NAME = os.getenv("DATABASE_NAME")
if not DATABASE_NAME:
    logger.fatal("DATABASE_NAME is not set in .env")


# Load screener configuration
SCREENER_PASSWORD = os.getenv("SCREENER_PASSWORD")
if not SCREENER_PASSWORD:
    logger.fatal("SCREENER_PASSWORD is not set in .env")

SCREENER_1_THRESHOLD = os.getenv("SCREENER_1_THRESHOLD")
if not SCREENER_1_THRESHOLD:
    logger.fatal("SCREENER_1_THRESHOLD is not set in .env")
SCREENER_1_THRESHOLD = float(SCREENER_1_THRESHOLD)

SCREENER_2_THRESHOLD = os.getenv("SCREENER_2_THRESHOLD")
if not SCREENER_2_THRESHOLD:
    logger.fatal("SCREENER_2_THRESHOLD is not set in .env")
SCREENER_2_THRESHOLD = float(SCREENER_2_THRESHOLD)

PRUNE_THRESHOLD = os.getenv("PRUNE_THRESHOLD")
if not PRUNE_THRESHOLD:
    logger.fatal("PRUNE_THRESHOLD is not set in .env")
PRUNE_THRESHOLD = float(PRUNE_THRESHOLD)


# Load validator configuration
VALIDATOR_HEARTBEAT_TIMEOUT_SECONDS = os.getenv("VALIDATOR_HEARTBEAT_TIMEOUT_SECONDS")
if not VALIDATOR_HEARTBEAT_TIMEOUT_SECONDS:
    logger.fatal("VALIDATOR_HEARTBEAT_TIMEOUT_SECONDS is not set in .env")
VALIDATOR_HEARTBEAT_TIMEOUT_SECONDS = int(VALIDATOR_HEARTBEAT_TIMEOUT_SECONDS)

VALIDATOR_HEARTBEAT_TIMEOUT_INTERVAL_SECONDS = os.getenv("VALIDATOR_HEARTBEAT_TIMEOUT_INTERVAL_SECONDS")
if not VALIDATOR_HEARTBEAT_TIMEOUT_INTERVAL_SECONDS:
    logger.fatal("VALIDATOR_HEARTBEAT_TIMEOUT_INTERVAL_SECONDS is not set in .env")
VALIDATOR_HEARTBEAT_TIMEOUT_INTERVAL_SECONDS = int(VALIDATOR_HEARTBEAT_TIMEOUT_INTERVAL_SECONDS)


# Load validator configuration (sent to validator upon registration)
VALIDATOR_RUNNING_AGENT_TIMEOUT_SECONDS = os.getenv("VALIDATOR_RUNNING_AGENT_TIMEOUT_SECONDS")
if not VALIDATOR_RUNNING_AGENT_TIMEOUT_SECONDS:
    logger.fatal("VALIDATOR_RUNNING_AGENT_TIMEOUT_SECONDS is not set in .env")
VALIDATOR_RUNNING_AGENT_TIMEOUT_SECONDS = int(VALIDATOR_RUNNING_AGENT_TIMEOUT_SECONDS)

VALIDATOR_RUNNING_EVAL_TIMEOUT_SECONDS = os.getenv("VALIDATOR_RUNNING_EVAL_TIMEOUT_SECONDS")
if not VALIDATOR_RUNNING_EVAL_TIMEOUT_SECONDS:
    logger.fatal("VALIDATOR_RUNNING_EVAL_TIMEOUT_SECONDS is not set in .env")
VALIDATOR_RUNNING_EVAL_TIMEOUT_SECONDS = int(VALIDATOR_RUNNING_EVAL_TIMEOUT_SECONDS)

VALIDATOR_MAX_EVALUATION_RUN_LOG_SIZE_BYTES = os.getenv("VALIDATOR_MAX_EVALUATION_RUN_LOG_SIZE_BYTES")
if not VALIDATOR_MAX_EVALUATION_RUN_LOG_SIZE_BYTES:
    logger.fatal("VALIDATOR_MAX_EVALUATION_RUN_LOG_SIZE_BYTES is not set in .env")
VALIDATOR_MAX_EVALUATION_RUN_LOG_SIZE_BYTES = int(VALIDATOR_MAX_EVALUATION_RUN_LOG_SIZE_BYTES)


MINER_AGENT_UPLOAD_RATE_LIMIT_SECONDS = os.getenv("MINER_AGENT_UPLOAD_RATE_LIMIT_SECONDS")
if not MINER_AGENT_UPLOAD_RATE_LIMIT_SECONDS:
    logger.fatal("MINER_AGENT_UPLOAD_RATE_LIMIT_SECONDS is not set in .env")
MINER_AGENT_UPLOAD_RATE_LIMIT_SECONDS = int(MINER_AGENT_UPLOAD_RATE_LIMIT_SECONDS)

NUM_EVALS_PER_AGENT = os.getenv("NUM_EVALS_PER_AGENT")
if not NUM_EVALS_PER_AGENT:
    logger.fatal("NUM_EVALS_PER_AGENT is not set in .env")
NUM_EVALS_PER_AGENT = int(NUM_EVALS_PER_AGENT)

SHOULD_RUN_LOOPS = os.getenv("SHOULD_RUN_LOOPS")
if not SHOULD_RUN_LOOPS:
    logger.fatal("SHOULD_RUN_LOOPS is not set in .env")
SHOULD_RUN_LOOPS = SHOULD_RUN_LOOPS.lower() == "true"

PRE_SCREENING_JUDGE_ENABLED = os.getenv("PRE_SCREENING_JUDGE_ENABLED", "false").lower() == "true"
PRE_SCREENING_JUDGE_RUN_LOOP = SHOULD_RUN_LOOPS and PRE_SCREENING_JUDGE_ENABLED
PRE_SCREENING_JUDGE_URL = os.getenv("PRE_SCREENING_JUDGE_URL")
PRE_SCREENING_JUDGE_INTERNAL_TOKEN = os.getenv("PRE_SCREENING_JUDGE_INTERNAL_TOKEN")

PRE_SCREENING_JUDGE_REQUEST_TIMEOUT_SECONDS = 720
if PRE_SCREENING_JUDGE_RUN_LOOP:
    configured_pre_screening_judge_timeout = os.getenv("PRE_SCREENING_JUDGE_REQUEST_TIMEOUT_SECONDS")
    if configured_pre_screening_judge_timeout:
        try:
            PRE_SCREENING_JUDGE_REQUEST_TIMEOUT_SECONDS = int(configured_pre_screening_judge_timeout)
        except ValueError:
            logger.fatal("PRE_SCREENING_JUDGE_REQUEST_TIMEOUT_SECONDS must be an integer")

    if not PRE_SCREENING_JUDGE_URL:
        logger.fatal("PRE_SCREENING_JUDGE_URL is not set in .env while the pre-screening judge loop is enabled")
    if not PRE_SCREENING_JUDGE_INTERNAL_TOKEN:
        logger.fatal(
            "PRE_SCREENING_JUDGE_INTERNAL_TOKEN is not set in .env while the pre-screening judge loop is enabled"
        )

SENTRY_DSN = os.getenv("SENTRY_DSN")
if not SENTRY_DSN:
    logger.warning("SENTRY_DSN is not set, Sentry will not be configured.")

logger.info("=== API Configuration ===")

logger.info(f"Host: {HOST}")
logger.info(f"Port: {PORT}")
logger.info("-------------------------")

logger.info(f"Network ID: {NETUID}")
logger.info(f"Subtensor Address: {SUBTENSOR_ADDRESS}")
logger.info(f"Subtensor Network: {SUBTENSOR_NETWORK}")
logger.info(f"Owner Hotkey: {OWNER_HOTKEY}")
logger.info("-------------------------")

if BURN:
    logger.warning("Burning!")
    logger.info("-------------------------")

if DISALLOW_UPLOADS:
    logger.warning(f"Disallowing Uploads: {DISALLOW_UPLOADS_REASON}")
    logger.info("-------------------------")

logger.info(f"Environment: {'Production' if ENV == 'prod' else 'Development'}")
logger.info("-------------------------")

logger.info(f"AWS Region: {AWS_REGION}")
logger.info("-------------------------")

logger.info(f"S3 Bucket Name: {S3_BUCKET_NAME}")
logger.info("-------------------------")

logger.info(f"S3 Endpoint URL: {S3_ENDPOINT_URL if S3_ENDPOINT_URL else 'Not set, using default AWS S3 endpoint'}")

logger.info(f"Database Username: {DATABASE_USERNAME}")
logger.info(f"Database Host: {DATABASE_HOST}")
logger.info(f"Database Port: {DATABASE_PORT}")
logger.info(f"Database Name: {DATABASE_NAME}")
logger.info("-------------------------")

logger.info(f"Screener 1 Threshold: {SCREENER_1_THRESHOLD}")
logger.info(f"Screener 2 Threshold: {SCREENER_2_THRESHOLD}")
logger.info(f"Prune Threshold: {PRUNE_THRESHOLD}")
logger.info("-------------------------")

logger.info(f"Validator Heartbeat Timeout: {VALIDATOR_HEARTBEAT_TIMEOUT_SECONDS} second(s)")
logger.info(f"Validator Heartbeat Timeout Interval: {VALIDATOR_HEARTBEAT_TIMEOUT_INTERVAL_SECONDS} second(s)")
logger.info("-------------------------")

logger.info(f"Validator Running Agent Timeout: {VALIDATOR_RUNNING_AGENT_TIMEOUT_SECONDS} second(s)")
logger.info(f"Validator Running Evaluation Timeout: {VALIDATOR_RUNNING_EVAL_TIMEOUT_SECONDS} second(s)")
logger.info(f"Validator Max Evaluation Run Log Size: {VALIDATOR_MAX_EVALUATION_RUN_LOG_SIZE_BYTES} byte(s)")
logger.info("-------------------------")

logger.info(f"Miner Agent Upload Rate Limit: {MINER_AGENT_UPLOAD_RATE_LIMIT_SECONDS} second(s)")
logger.info(f"Number of Evaluations per Agent: {NUM_EVALS_PER_AGENT}")
logger.info(f"Pre-Screening Judge Enabled: {PRE_SCREENING_JUDGE_ENABLED}")
logger.info(f"Pre-Screening Judge Loop Enabled: {PRE_SCREENING_JUDGE_RUN_LOOP}")
if PRE_SCREENING_JUDGE_RUN_LOOP:
    logger.info(f"Pre-Screening Judge URL: {PRE_SCREENING_JUDGE_URL}")
    logger.info(f"Pre-Screening Judge Request Timeout: {PRE_SCREENING_JUDGE_REQUEST_TIMEOUT_SECONDS} second(s)")

logger.info("=========================")
