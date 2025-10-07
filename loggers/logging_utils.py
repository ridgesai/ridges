import logging
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

from loggers.process_tracking import setup_process_logging

load_dotenv()

# Flags to track if Datadog logging has been initialized
_datadog_initialized = False
_datadog_handler = None

class TimestampFilter(logging.Filter):
    """Add high-precision timestamp to all log records"""
    def filter(self, record):
        record.timestamp = datetime.now(timezone.utc)
        return True

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


root_logger = logging.getLogger()
for handler in root_logger.handlers:
    if isinstance(handler, logging.StreamHandler):
        handler.setLevel(logging.INFO)

logger = logging.getLogger(__name__)

def get_logger(name: str):
    logger = logging.getLogger(name)
    
    logger.addFilter(TimestampFilter())
    
    setup_process_logging(logger)
    
    return logger
