import httpx
from loggers.logging_utils import get_logger
from validator.tasks.set_weights import set_weights_from_mapping
from validator.utils.http_client import get_shared_client
from validator.config import RIDGES_API_URL
from ddtrace import tracer

logger = get_logger(__name__)

@tracer.wrap(resource="handle-set-weights")
async def handle_set_weights(websocket_app, json_message):
    """Handle a `set-weights` websocket event.
    
    Fetches weights from the API endpoint and sets them on chain
    """
    logger.info("Received set-weights event – fetching weights from API")

    try:
        # Fetch weights from the API endpoint
        async with get_shared_client() as client:
            response = await client.get(f"{RIDGES_API_URL}/scoring/weights")
            response.raise_for_status()
            weights_mapping = response.json()
        
        logger.info(f"Retrieved weights mapping with {len(weights_mapping)} hotkeys")
        
        # Set weights on chain according to the mapping
        await set_weights_from_mapping(weights_mapping)
        logger.info("Successfully processed set-weights event")
        
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error fetching weights: {e.response.status_code} - {e.response.text}")
    except httpx.RequestError as e:
        logger.error(f"Request error fetching weights: {e}")
    except Exception as e:
        logger.error(f"Failed to process set-weights event: {e}") 