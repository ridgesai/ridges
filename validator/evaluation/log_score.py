from typing import Literal
import httpx

from shared.logging_utils import get_logger
from validator.config import RIDGES_API_URL

logger = get_logger(__name__)

async def log_score(type: Literal["trueskill", "float_grader", "elo_grader", "weight"], validator_hotkey: str, miner_hotkey: str, score: float):
    try:
        score_data = {
            "type": type,
            "validator_hotkey": validator_hotkey,
            "miner_hotkey": miner_hotkey,
            "score": score,
        }
        
        async with httpx.AsyncClient() as client:
            logger.info(f"Logging {type} score for miner {miner_hotkey}: {score}")
            response = await client.post(
                f"{RIDGES_API_URL}/ingestion/scores",
                json=score_data,
                timeout=30.0
            )
            response.raise_for_status()
            logger.info(f"Successfully logged {type} score for miner {miner_hotkey}: {score}")
            
    except Exception as e:
        logger.error(f"Failed to log score to Ridges API: {str(e)}")

