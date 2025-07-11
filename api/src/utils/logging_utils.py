import logging
from datadog.datadog import DatadogLogHandler
from api.src.utils.process_tracking import setup_process_logging

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

    setup_process_logging(logger)

    datadog_handler = DatadogLogHandler()
    logger.addHandler(datadog_handler)
    
    return logger
