import logging
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

from utils.process_tracking import setup_process_logging

load_dotenv()


class TimestampFilter(logging.Filter):
    """Add high-precision timestamp to all log records"""
    def filter(self, record):
        record.timestamp = datetime.now(timezone.utc)
        return True

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper()),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Suppress debug spam from third-party libraries  
logging.getLogger('websockets').setLevel(logging.WARNING)
logging.getLogger('asyncio').setLevel(logging.WARNING)
logging.getLogger('docker').setLevel(logging.WARNING)

root_logger = logging.getLogger()
for handler in root_logger.handlers:
    if isinstance(handler, logging.StreamHandler):
        handler.setLevel(getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper()))

logger = logging.getLogger(__name__)

def get_logger(name: str):
    logger = logging.getLogger(name)
    logger.addFilter(TimestampFilter())
    setup_process_logging(logger)
    return logger
