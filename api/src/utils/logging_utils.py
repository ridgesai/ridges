import logging
from datadog.datadog import DatadogLogHandler
from api.src.utils.process_tracking import setup_process_logging

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

    setup_process_logging(logger)

    datadog_handler = DatadogLogHandler()
    logger.addHandler(datadog_handler)
    
    return logger
