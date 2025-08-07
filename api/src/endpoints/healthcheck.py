from fastapi import APIRouter, HTTPException
from typing import List

from api.src.backend.queries.healthcheck import get_healthcheck_results
from api.src.socket.server_helpers import SERVER_COMMIT_HASH
from loggers.logging_utils import get_logger

logger = get_logger(__name__)

async def healthcheck():
    return "OK"

async def healthcheck_results(limit: int = 30) -> List[dict]:
    try:
        healthcheck_results = await get_healthcheck_results(limit)
    except Exception as e:
        logger.error(f"Error getting healthcheck results: {e}")
        raise HTTPException(status_code=500, detail="Failed to get healthcheck results")
    
    return healthcheck_results

async def version():
    return SERVER_COMMIT_HASH

router = APIRouter()

routes = [
    ("/healthcheck", healthcheck),
    ("/healthcheck-results", healthcheck_results),
    ("/version", version),
]

for path, endpoint in routes:
    router.add_api_route(
        path,
        endpoint,
        tags=["healthcheck"],
        methods=["GET"]
    )
