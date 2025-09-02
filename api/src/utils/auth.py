from fastapi import Request, Header, Depends, HTTPException
from api.src.utils.config import WHITELISTED_VALIDATOR_IPS
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
    """Verify request using IP whitelist for protected endpoints"""
    client_ip = request.client.host if request.client else None
    
    if not client_ip:
        logger.warning("Request received without client IP information")
        raise HTTPException(
            status_code=403,
            detail="Unable to determine client IP address"
        )
    
    if not WHITELISTED_VALIDATOR_IPS:
        # Empty whitelist = allow all IPs (warning logged at startup)
        return True
    
    if client_ip not in WHITELISTED_VALIDATOR_IPS:
        logger.warning(f"Request from non-whitelisted IP: {client_ip}. Whitelisted IPs: {WHITELISTED_VALIDATOR_IPS}")
        raise HTTPException(
            status_code=403,
            detail="Access denied: IP not whitelisted"
        )
    
    logger.debug(f"Request from whitelisted IP: {client_ip}")
    return True

async def verify_request_public(request: Request):
    """Allow all requests for public endpoints"""
    return True

# Backwards compatibility - use IP whitelist by default
async def verify_request(request: Request):
    """Default verification - uses IP whitelist"""
    return await verify_request_ip_whitelist(request)
