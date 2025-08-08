"""Handler for get validator info events with cryptographic authentication."""

import subprocess
import time
from loggers.logging_utils import get_logger
from ddtrace import tracer

from validator.config import validator_hotkey

logger = get_logger(__name__)

VERSION_COMMIT_HASH = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode("utf-8").strip()

@tracer.wrap(resource="get-validator-info")
def get_validator_info():
    """Generate validator info with cryptographic proof of hotkey ownership."""
    if validator_hotkey is None:
        logger.error("Validator hotkey is not configured")
        return {
            "event": "validator-info",
            "error": "Validator hotkey not configured"
        }
    
    # Create a message that includes timestamp, hotkey, and version
    timestamp = int(time.time())
    message = f"validator-auth:{validator_hotkey.ss58_address}:{VERSION_COMMIT_HASH}:{timestamp}"
    
    # Sign the message with the validator's private key
    signature = validator_hotkey.sign(message).hex()
    
    return {
        "event": "validator-info",
        "validator_hotkey": validator_hotkey.ss58_address,
        "public_key": validator_hotkey.public_key.hex(),
        "signature": signature,
        "message": message,
        "version_commit_hash": VERSION_COMMIT_HASH,
        "timestamp": timestamp
    }
