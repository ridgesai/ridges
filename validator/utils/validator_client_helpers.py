"""
Helper functions for the validator client.

This module provides additional functionality for the validator client,
particularly for the new queue-based miner support.
"""

import httpx
from fiber import Keypair
from fiber.validator import client as validator_client
from shared.logging_utils import get_logger

logger = get_logger(__name__)

async def make_non_streamed_get(
    httpx_client: httpx.AsyncClient,
    server_address: str,
    validator_ss58_address: str,
    miner_ss58_address: str,
    keypair: Keypair,
    endpoint: str,
    timeout: float = 30.0
) -> httpx.Response:
    """
    Make a non-streamed GET request to a miner node with proper authentication.
    
    Args:
        httpx_client: The HTTP client to use for the request.
        server_address: The miner server address.
        validator_ss58_address: The validator's SS58 address.
        miner_ss58_address: The miner's SS58 address.
        keypair: The validator's keypair for signing.
        endpoint: The endpoint to call.
        timeout: Request timeout in seconds.
        
    Returns:
        The HTTP response.
    """
    # Reuse the existing make_request function from validator_client
    # but change the method to GET
    return await validator_client.make_request(
        httpx_client=httpx_client,
        server_address=server_address,
        validator_ss58_address=validator_ss58_address,
        miner_ss58_address=miner_ss58_address,
        keypair=keypair,
        endpoint=endpoint,
        method="GET",
        payload=None,  # No payload for GET
        timeout=timeout
    ) 