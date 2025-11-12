import json
import httpx
import textwrap
import utils.logger as logger
import validator.config as config

from typing import Any
from pydantic import BaseModel



# TODO ADAM: .env
HTTP_TIMEOUT_SECONDS = 120



def _pretty_print_httpx_error(method: str, url: str, e: httpx.HTTPStatusError):
    # HTTP error (4xx or 5xx)
    logger.error(f"HTTP {e.response.status_code} {e.response.reason_phrase} during {method} {url}")

    # Try and print the response as best as we can
    try:
        response_json = e.response.json()
        if isinstance(response_json, dict) and len(response_json) == 1 and "detail" in response_json and isinstance(response_json["detail"], str):
            # The response is a JSON that looks like {"detail": "..."}
            logger.error(textwrap.indent(response_json["detail"], "  "))
        else:
            # The response is a JSON
            logger.error(f"Response (JSON):")
            logger.error(textwrap.indent(json.dumps(response_json, indent=2), "  "))
    except Exception:
        # The response is not a JSON
        logger.error(f"Response:")
        logger.error(textwrap.indent(e.response.text, "  "))



async def get_ridges_platform(endpoint: str, *, quiet: int = 0) -> Any:
    """
    Helper function that sends a GET request to the Ridges platform.

    Args:
        endpoint: The endpoint to send the request to. You do not need to specify the Ridges platform URL, just something like `/evaluation-sets/all-latest-set-problems`.
        quiet: The level of quietness.
               - 0: Print all debugging information, including the request and response bodies.
               - 1: Print debugging information, but exclude the request and response bodies.
               - 2: Print no debugging information, except for errors.

    Returns:
        The response from the Ridges platform. If the request returns a non-2xx status code, the function will print the error and exit the program.
    """



    url = f"{config.RIDGES_PLATFORM_URL.rstrip('/')}/{endpoint.lstrip('/')}"

    if quiet <= 1:
        logger.debug(f"Sending request for GET {url}")

    try:
        # Send the request
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            response = await client.get(url)
            response.raise_for_status()
            response_json = response.json()

            if quiet <= 1:
                logger.debug(f"Received response for GET {url}: {response.status_code} {response.reason_phrase}")
            if response_json != {} and quiet == 0:
                logger.debug(textwrap.indent(json.dumps(response_json, indent=2), "  "))
            
            return response.json()
    
    except httpx.HTTPStatusError as e:
        _pretty_print_httpx_error("GET", url, e)
        
        raise
    
    except Exception as e:
        # Internal error (timeout, DNS error, etc.)
        logger.error(f"{type(e).__name__} during GET {url}")

        raise



async def post_ridges_platform(endpoint: str, body: BaseModel, *, bearer_token: str = None, quiet: int = 0) -> Any:
    """
    Helper function that sends a POST request to the Ridges platform.

    Args:
        endpoint: The endpoint to send the request to. You do not need to specify the Ridges platform URL, just something like `/validator/register`.
        body: The body of the request (as a Pydantic object), which will be serialized to JSON before being sent.
        bearer_token: The bearer token to use for the request, which will be sent as an `Authorization: Bearer` header. By default, this is `None`, and the header is not sent.
        quiet: The level of quietness.
               - 0: Print all debugging information, including the request and response bodies.
               - 1: Print debugging information, but exclude the request and response bodies.
               - 2: Print no debugging information, except for errors.

    Returns:
        The response from the Ridges platform. If the request returns a non-2xx status code, the function will print the error and exit the program.
    """



    url = f"{config.RIDGES_PLATFORM_URL.rstrip('/')}/{endpoint.lstrip('/')}"

    body_dict = body.model_dump(mode="json")

    if quiet <= 1:
        logger.debug(f"Sending request for POST {url}")
    if body_dict != {} and quiet == 0:
        logger.debug(textwrap.indent(json.dumps(body_dict, indent=2), "  "))
    
    try:
        # Send the request
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token is not None else None
            response = await client.post(url, json=body_dict, headers=headers)
            response.raise_for_status()
            response_json = response.json()

            if quiet <= 1:
                logger.debug(f"Received response for POST {url}: {response.status_code} {response.reason_phrase}")
            if response_json != {} and quiet == 0:
                logger.debug(textwrap.indent(json.dumps(response_json, indent=2), "  "))
            
            return response.json()
    
    except httpx.HTTPStatusError as e:
        _pretty_print_httpx_error("POST", url, e)
        
        raise
    
    except Exception as e:
        # Internal error (timeout, DNS error, etc.)
        logger.error(f"{type(e).__name__} during POST {url}")

        raise