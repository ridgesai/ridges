import logging
from datadog.datadog import DatadogLogHandler

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

def get_logger(name: str):
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    logger = logging.getLogger(name)
    datadog_handler = DatadogLogHandler()
    logger.addHandler(datadog_handler)
    return logger
