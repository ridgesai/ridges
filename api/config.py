import os
import utils.logger as logger
from dotenv import load_dotenv



# TODO ADAM
# This isn't actually called anywhere afaik, eventually need to replace all other places that load os.getenv directly with this.
# redundantly define all envvars and then manually load each one, rather should iterate over the ENV_VARS dictionary and load each one.
#    probably do some hack shit to do that
#    sys.modules[__name__].__dict__["THE_VARIABLE_NAME"] = "THE_VALUE"






# Load everything from .env
load_dotenv()

# Define all expected environment variables with their validation requirements
ENV_VARS = {
    "ENV": {"type": str, "required": False},
    "NETUID": {"type": int, "required": True},
    "SUBTENSOR_NETWORK": {"type": str, "required": True},
    "SUBTENSOR_ADDRESS": {"type": str, "required": True},
    "AWS_MASTER_USERNAME": {"type": str, "required": True},
    "AWS_MASTER_PASSWORD": {"type": str, "required": True},
    "AWS_RDS_PLATFORM_ENDPOINT": {"type": str, "required": True},
    "AWS_RDS_PLATFORM_DB_NAME": {"type": str, "required": True},
    "AWS_S3_BUCKET_NAME": {"type": str, "required": True},
    "AWS_REGION": {"type": str, "required": True},
    "AWS_ACCESS_KEY_ID": {"type": str, "required": True},
    "AWS_SECRET_ACCESS_KEY": {"type": str, "required": True},
    "CHUTES_API_KEY": {"type": str, "required": True},
    "POSTHOG_API_KEY": {"type": str, "required": True},
    "POSTHOG_HOST": {"type": str, "required": True},
    "SLACK_BOT_TOKEN": {"type": str, "required": True},
    "SLACK_APP_TOKEN": {"type": str, "required": True},
    "APPROVAL_PASSWORD": {"type": str, "required": True},
}

def check_env_var(var_name: str, value: str):
    # Check if the environment variable is in the ENV_VARS dictionary
    if var_name not in ENV_VARS:
        logger.error(f"Invalid environment variable: {var_name}")
        raise SystemExit(1)
    config = ENV_VARS[var_name]
    if config["required"] and not value:
        logger.error(f"{var_name} is not set in .env")
        raise SystemExit(1)
    if config["type"] == int and value is not None:
        try:
            value = int(value)
        except ValueError:
            logger.error(f"{var_name} must be an integer, got: {value}")
            raise SystemExit(1)

# Environment configuration
ENV = os.getenv("ENV")
check_env_var("ENV", ENV)

NETUID = os.getenv("NETUID")
check_env_var("NETUID", NETUID)

SUBTENSOR_NETWORK = os.getenv("SUBTENSOR_NETWORK")
check_env_var("SUBTENSOR_NETWORK", SUBTENSOR_NETWORK)

SUBTENSOR_ADDRESS = os.getenv("SUBTENSOR_ADDRESS")
check_env_var("SUBTENSOR_ADDRESS", SUBTENSOR_ADDRESS)

AWS_MASTER_USERNAME = os.getenv("AWS_MASTER_USERNAME")
check_env_var("AWS_MASTER_USERNAME", AWS_MASTER_USERNAME)

AWS_MASTER_PASSWORD = os.getenv("AWS_MASTER_PASSWORD")
check_env_var("AWS_MASTER_PASSWORD", AWS_MASTER_PASSWORD)

AWS_RDS_PLATFORM_ENDPOINT = os.getenv("AWS_RDS_PLATFORM_ENDPOINT")
check_env_var("AWS_RDS_PLATFORM_ENDPOINT", AWS_RDS_PLATFORM_ENDPOINT)

AWS_RDS_PLATFORM_DB_NAME = os.getenv("AWS_RDS_PLATFORM_DB_NAME")
check_env_var("AWS_RDS_PLATFORM_DB_NAME", AWS_RDS_PLATFORM_DB_NAME)


AWS_S3_BUCKET_NAME = os.getenv("AWS_S3_BUCKET_NAME")
check_env_var("AWS_S3_BUCKET_NAME", AWS_S3_BUCKET_NAME)

AWS_REGION = os.getenv("AWS_REGION")
check_env_var("AWS_REGION", AWS_REGION)

AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
check_env_var("AWS_ACCESS_KEY_ID", AWS_ACCESS_KEY_ID)

AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
check_env_var("AWS_SECRET_ACCESS_KEY", AWS_SECRET_ACCESS_KEY)

CHUTES_API_KEY = os.getenv("CHUTES_API_KEY")
check_env_var("CHUTES_API_KEY", CHUTES_API_KEY)

POSTHOG_API_KEY = os.getenv("POSTHOG_API_KEY")
check_env_var("POSTHOG_API_KEY", POSTHOG_API_KEY)

POSTHOG_HOST = os.getenv("POSTHOG_HOST")
check_env_var("POSTHOG_HOST", POSTHOG_HOST)

# Slack Configuration
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
check_env_var("SLACK_BOT_TOKEN", SLACK_BOT_TOKEN)

SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")
check_env_var("SLACK_APP_TOKEN", SLACK_APP_TOKEN)

# Security/Passwords
APPROVAL_PASSWORD = os.getenv("APPROVAL_PASSWORD")
check_env_var("APPROVAL_PASSWORD", APPROVAL_PASSWORD)

logger.info("=== API Configuration Loaded ===")
logger.info(f"Environment: {ENV}")
logger.info(f"NetUID: {NETUID}")
logger.info(f"Subtensor Network: {SUBTENSOR_NETWORK}")
logger.info(f"Subtensor Address: {SUBTENSOR_ADDRESS}")
logger.info(f"AWS Region: {AWS_REGION}")
logger.info(f"S3 Bucket: {AWS_S3_BUCKET_NAME}")
logger.info(f"Platform DB: {AWS_RDS_PLATFORM_DB_NAME}")
logger.info(f"Internal DB: {DB_NAME_INT}")
logger.info("=== Configuration Complete ===")