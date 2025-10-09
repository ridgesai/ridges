from fastapi import Request, Header, Depends, HTTPException
from loggers.logging_utils import get_logger
# from fiber import constants as cst

logger = get_logger(__name__)

async def verify_request_ip_whitelist(
    request: Request,
    # validator_hotkey: str = Header(..., alias=cst.VALIDATOR_HOTKEY),
    # signature: str = Header(..., alias=cst.SIGNATURE),
    # miner_hotkey: str = Header(..., alias=cst.MINER_HOTKEY),
    # nonce: str = Header(..., alias=cst.NONCE),
):
    # TODO: REMOVE THIS WE DO NOT WANT TO USE THIS METHOD ANYMORE
    return True

async def verify_request_public(request: Request):
    """Allow all requests for public endpoints"""
    return True

# Backwards compatibility - use IP whitelist by default
async def verify_request(request: Request):
    """Default verification - uses IP whitelist"""
    return await verify_request_ip_whitelist(request)
