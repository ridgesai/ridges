import logging
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

from loggers.datadog import DatadogLogHandler
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

# Suppress ddtrace errors about failing to send traces
logging.getLogger('ddtrace.internal.writer.writer').setLevel(logging.CRITICAL)

root_logger = logging.getLogger()
for handler in root_logger.handlers:
    if isinstance(handler, logging.StreamHandler):
        handler.setLevel(logging.INFO)

logger = logging.getLogger(__name__)

def get_logger(name: str):
    global _datadog_initialized, _datadog_handler
    
    logger = logging.getLogger(name)
    
    logger.addFilter(TimestampFilter())
    
    setup_process_logging(logger)

    # Only initialize Datadog logging once per program run
    if not _datadog_initialized:
        if os.getenv("DD_API_KEY") and os.getenv("DD_APP_KEY") and os.getenv("DD_HOSTNAME") and os.getenv("DD_SITE") and os.getenv("DD_ENV"):
            _datadog_handler = DatadogLogHandler()
            print("Datadog logging enabled")
        else:
            print("No Datadog API key or app key found, skipping Datadog logging")
        _datadog_initialized = True
    
    # Add Datadog handler to all loggers if it was created
    if _datadog_handler is not None:
        logger.addHandler(_datadog_handler)
        logger.setLevel(logging.DEBUG)  # Allow DEBUG messages for this specific logger
        _datadog_handler.setLevel(logging.DEBUG)
        
        # Set all other handlers to INFO level
        for handler in logger.handlers:
            if not isinstance(handler, DatadogLogHandler):
                handler.setLevel(logging.INFO)
    
    return logger
