import os
import utils.logger as logger

from dotenv import load_dotenv



# Load everything from .env
load_dotenv()



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



# Load S3 configuration
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
if not S3_BUCKET_NAME:
    logger.fatal("S3_BUCKET_NAME is not set in .env")



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
    
DATABASE_PORT = int(os.getenv("DATABASE_PORT"))
if not DATABASE_PORT:
    logger.fatal("DATABASE_PORT is not set in .env")
    
DATABASE_NAME = os.getenv("DATABASE_NAME")
if not DATABASE_NAME:
    logger.fatal("DATABASE_NAME is not set in .env")



# Load screener configuration
SCREENER_PASSWORD = os.getenv("SCREENER_PASSWORD")
if not SCREENER_PASSWORD:
    logger.fatal("SCREENER_PASSWORD is not set in .env")

SCREENER_1_THRESHOLD = float(os.getenv("SCREENER_1_THRESHOLD"))
if not SCREENER_1_THRESHOLD:
    logger.fatal("SCREENER_1_THRESHOLD is not set in .env")

SCREENER_2_THRESHOLD = float(os.getenv("SCREENER_2_THRESHOLD"))
if not SCREENER_2_THRESHOLD:
    logger.fatal("SCREENER_2_THRESHOLD is not set in .env")

PRUNE_THRESHOLD = float(os.getenv("PRUNE_THRESHOLD"))
if not PRUNE_THRESHOLD:
    logger.fatal("PRUNE_THRESHOLD is not set in .env")


# Load validator configuration
VALIDATOR_HEARTBEAT_TIMEOUT_SECONDS = int(os.getenv("VALIDATOR_HEARTBEAT_TIMEOUT_SECONDS"))
if not VALIDATOR_HEARTBEAT_TIMEOUT_SECONDS:
    logger.fatal("VALIDATOR_HEARTBEAT_TIMEOUT_SECONDS is not set in .env")

VALIDATOR_HEARTBEAT_TIMEOUT_INTERVAL_SECONDS = int(os.getenv("VALIDATOR_HEARTBEAT_TIMEOUT_INTERVAL_SECONDS"))
if not VALIDATOR_HEARTBEAT_TIMEOUT_INTERVAL_SECONDS:
    logger.fatal("VALIDATOR_HEARTBEAT_TIMEOUT_INTERVAL_SECONDS is not set in .env")

NUM_EVALS_PER_AGENT = int(os.getenv("NUM_EVALS_PER_AGENT"))
if not NUM_EVALS_PER_AGENT:
    logger.fatal("NUM_EVALS_PER_AGENT is not set in .env")



MINER_AGENT_UPLOAD_RATE_LIMIT_SECONDS = int(os.getenv("MINER_AGENT_UPLOAD_RATE_LIMIT_SECONDS"))
if not MINER_AGENT_UPLOAD_RATE_LIMIT_SECONDS:
    logger.fatal("MINER_AGENT_UPLOAD_RATE_LIMIT_SECONDS is not set in .env")

logger.info("=== API Configuration =================")
logger.info(f"ENV: {ENV}")
logger.info("-------------------------------")
logger.info(f"AWS_REGION: {AWS_REGION}")
logger.info(f"S3_BUCKET_NAME: {S3_BUCKET_NAME}")
logger.info("-------------------------------")
logger.info(f"DATABASE_USERNAME: {DATABASE_USERNAME}")
logger.info(f"DATABASE_HOST: {DATABASE_HOST}")
logger.info(f"DATABASE_PORT: {DATABASE_PORT}")
logger.info(f"DATABASE_NAME: {DATABASE_NAME}")
logger.info("-------------------------------")
logger.info(f"SCREENER_1_THRESHOLD: {SCREENER_1_THRESHOLD}")
logger.info(f"SCREENER_2_THRESHOLD: {SCREENER_2_THRESHOLD}")
logger.info(f"PRUNE_THRESHOLD: {PRUNE_THRESHOLD}")
logger.info(f"MINER_AGENT_UPLOAD_RATE_LIMIT_SECONDS: {MINER_AGENT_UPLOAD_RATE_LIMIT_SECONDS}")
logger.info(f"NUM_EVALS_PER_AGENT: {NUM_EVALS_PER_AGENT}")
logger.info("-------------------------------")
logger.info(f"VALIDATOR_HEARTBEAT_TIMEOUT_SECONDS: {VALIDATOR_HEARTBEAT_TIMEOUT_SECONDS}")
logger.info(f"VALIDATOR_HEARTBEAT_TIMEOUT_LOOP_INTERVAL_SECONDS: {VALIDATOR_HEARTBEAT_TIMEOUT_LOOP_INTERVAL_SECONDS}")
logger.info("=======================================")