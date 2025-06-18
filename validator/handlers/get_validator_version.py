"""Handler for get validator version events."""

import json
import subprocess
from shared.logging_utils import get_logger

from validator.config import validator_hotkey

logger = get_logger(__name__)

VERSION_COMMIT_HASH = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode("utf-8").strip()

async def handle_get_validator_version(websocket):
    """Handle get validator version events."""
    await websocket.send(json.dumps({
        "event": "validator-version",
        "version_commit_hash": VERSION_COMMIT_HASH,
        "validator_hotkey": validator_hotkey.ss58_address
    })) 