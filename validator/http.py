import json
import httpx
import textwrap
import utils.logger as logger
import validator.config as config



async def post_ridges_platform(endpoint: str, body: dict = {}, *, bearer_token: str = None) -> httpx.Response:
    """
    Helper function that sends a POST request to the Ridges platform.

    Args:
        endpoint: The endpoint to send the request to. You do not need to specify the Ridges platform URL, just something like `/validator/register`.
        body: The body of the request, which will be sent as JSON. By default, this is an empty dictionary.
        bearer_token: The bearer token to use for the request, which will be sent as an `Authorization: Bearer` header. By default, this is `None`, and the header is not sent.

    Returns:
        The response from the Ridges platform. If the request returns a non-2xx status code, the function will print the error and exit the program.
    """

    url = f"{config.RIDGES_PLATFORM_URL.rstrip("/")}/{endpoint.lstrip("/")}"

    logger.debug(f"Sending request for POST {url}")
    if body != {}:
        logger.debug(textwrap.indent(json.dumps(body, indent=2), "  "))
    
    try:
        # Send the request
        async with httpx.AsyncClient() as client:
            headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token is not None else None
            response = await client.post(url, json=body, headers=headers)
            response.raise_for_status()

            logger.debug(f"Received response for POST {url}: {response.status_code} {response.reason_phrase}")
            logger.debug(textwrap.indent(json.dumps(response.json(), indent=2), "  "))
            
            return response
    
    except httpx.HTTPStatusError as e:
        # HTTP error (4xx or 5xx)
        logger.error(f"HTTP {e.response.status_code} {e.response.reason_phrase} during POST {url}")

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
        
        exit(1)
    
    except Exception as e:
        # Internal error (timeout, DNS error, etc.)
        logger.error(f"{type(e).__name__} during POST {url}")

        exit(1)