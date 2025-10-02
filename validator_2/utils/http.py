import json
import httpx
import config

import utils.logger as logger



async def post_ridges_platform(endpoint: str, body: dict = {}) -> httpx.Response:
    logger.debug(f"Sending request for POST {config.RIDGES_PLATFORM_URL.rstrip('/')}/{endpoint.lstrip('/')}\n{json.dumps(body, indent=2)}")
    url = f"{config.RIDGES_PLATFORM_URL.rstrip("/")}/{endpoint.lstrip("/")}"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=body)
            response.raise_for_status()
            logger.debug(f"Received response for POST {config.RIDGES_PLATFORM_URL.rstrip('/')}/{endpoint.lstrip('/')}: {response.status_code} {response.reason_phrase}\n{json.dumps(response.json(), indent=2)}")
            return response
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP {e.response.status_code} {e.response.reason_phrase} during POST {url}")
        try:
            response_json = e.response.json()
            logger.error(f"Response (JSON):\n{json.dumps(response_json, indent=2)}")
        except Exception:
            logger.error(f"Response:\n{e.response.text}")
        exit(1)
    except Exception as e:
        logger.error(f"{type(e).__name__} during POST {url}")
        exit(1)