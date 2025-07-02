"""Handler for get validator version events."""

import subprocess
from validator.utils.logging import get_logger

from validator.config import validator_hotkey

logger = get_logger(__name__)

VERSION_COMMIT_HASH = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode("utf-8").strip()

def get_validator_version_info():
    return {
        "event": "validator-version",
        "version_commit_hash": VERSION_COMMIT_HASH,
        "validator_hotkey": validator_hotkey.ss58_address
    }
