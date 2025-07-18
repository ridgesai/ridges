"""Handler for get validator info events with cryptographic authentication."""

import os
import subprocess
from loggers.logging_utils import get_logger
from ddtrace import tracer

from validator.config import aws_instance_id

logger = get_logger(__name__)

VERSION_COMMIT_HASH = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode("utf-8").strip()

@tracer.wrap(resource="get-screener-info")
def get_screener_info():
    """Generate screener info"""
    return {
        "event": "validator-info",
        "validator_hotkey": aws_instance_id,
        "version_commit_hash": VERSION_COMMIT_HASH,
    }
