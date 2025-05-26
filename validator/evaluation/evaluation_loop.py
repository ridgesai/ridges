from pathlib import Path

from validator.utils.logging_utils import get_logger
from openai import OpenAI

from validator.db.operations import DatabaseManager
from validator.evaluation.evaluation import CodeGenValidator

logger = get_logger(__name__)

async def run_evaluation_loop(
    db_path: Path,
    openai_client: OpenAI,
    validator_hotkey: str,
    batch_size: int = 10,
    sleep_interval: int = 60
) -> None:
    """Entrypoint that sets up the DB, validator, and runs the loop."""
    try: 
        logger.info("Initializing evaluation loop...")
        db_manager = DatabaseManager(db_path)
        validator = CodeGenValidator(openai_client, validator_hotkey)


    except Exception as e:
        logger.error(f"Fatal error in run_evaluation_loop: {str(e)}")
        logger.error("Stack trace:", exc_info=True)
        raise  # Re-raise the exception to trigger the task's error callback