import logging
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

from loggers.datadog import DatadogLogHandler
from loggers.process_tracking import setup_process_logging

load_dotenv()

class TimestampFilter(logging.Filter):
    """Add high-precision timestamp to all log records"""
    def filter(self, record):
        record.timestamp = datetime.now(timezone.utc)
        return True

logging.basicConfig(
    level=logging.DEBUG,
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

    if os.getenv("DD_API_KEY") and os.getenv("DD_APP_KEY") and os.getenv("DD_HOSTNAME") and os.getenv("DD_SITE"):
        datadog_handler = DatadogLogHandler()
        logger.addHandler(datadog_handler)
        print("Datadog logging enabled")
    else:
        print("No Datadog API key or app key found, skipping Datadog logging")
    
    return logger
