"""Handler for get validator info events with cryptographic authentication."""

import os
import subprocess
from validator.utils.logging import get_logger

from validator.config import validator_hotkey

logger = get_logger(__name__)

VERSION_COMMIT_HASH = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode("utf-8").strip()

def get_screener_info():
    """Generate screener info"""
    aws_id = os.getenv("AWS_INSTANCE_ID")
    return {
        "event": "validator-info",
        "validator_hotkey": aws_id,
        "public_key": validator_hotkey.public_key.hex(),
        "version_commit_hash": VERSION_COMMIT_HASH,
    }
