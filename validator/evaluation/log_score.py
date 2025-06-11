from dataclasses import dataclass, asdict
from typing import List, Literal
from typing import Optional
import httpx

from shared.logging_utils import get_logger
from validator.config import RIDGES_API_URL

logger = get_logger(__name__)


@dataclass
class ScoreLog:
    type: Literal["trueskill", "float_grader", "elo_grader", "weight"]
    challenge_id: Optional[str] = None
    validator_hotkey: str
    miner_hotkey: str
    score: float


async def log_scores(logs: List[ScoreLog]):
    if len(logs) == 0:
        return

    try:
        log_info = f"{len(logs)} {logs[0].type} scores for miner {logs[0].miner_hotkey}"
        async with httpx.AsyncClient() as client:
            logger.debug(f"Logging {log_info}")
            response = await client.post(
                f"{RIDGES_API_URL}/ingestion/scores", json=[asdict(log) for log in logs]
            )
            response.raise_for_status()
            logger.info(f"Successfully logged {log_info}")

    except Exception as e:
        logger.error(f"Failed to log {log_info} to Ridges API: {str(e)}")
