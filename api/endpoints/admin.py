import secrets
from typing import Annotated

from bittensor_wallet.keypair import Keypair
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, StringConstraints

import api.config as config
from api.endpoints import validator as validator_endpoint
from db.models import InternalFlagName
from models.banned_coldkey import BannedColdkey
from queries.banned_coldkey import ban_coldkey, unban_coldkey
from queries.internal_flag import add_hotkey_to_blacklist, remove_hotkey_from_blacklist, set_internal_flag
from utils.debug_lock import DebugLock
from utils.ttl import clear_all_ttl_caches

router = APIRouter(tags=["admin"])
admin_bearer = HTTPBearer(auto_error=False)


class ColdkeyBanRequest(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


def require_coldkey_ban_admin(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(admin_bearer)],
) -> None:
    expected = config.COLDKEY_BAN_ADMIN_API_KEY
    if not expected:
        raise HTTPException(status_code=503, detail="Coldkey ban administration is not configured")
    if credentials is None or not secrets.compare_digest(credentials.credentials, expected):
        raise HTTPException(
            status_code=401,
            detail="Invalid admin credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


def validate_coldkey(miner_coldkey: str) -> None:
    try:
        Keypair(ss58_address=miner_coldkey)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid coldkey SS58 address") from None


@router.put(
    "/banned-coldkeys/{miner_coldkey}",
    response_model=BannedColdkey,
    dependencies=[Depends(require_coldkey_ban_admin)],
)
async def put_banned_coldkey(miner_coldkey: str, request: ColdkeyBanRequest) -> BannedColdkey:
    validate_coldkey(miner_coldkey)
    banned_coldkey = await ban_coldkey(miner_coldkey, request.reason)
    clear_all_ttl_caches()
    return banned_coldkey


@router.delete(
    "/banned-coldkeys/{miner_coldkey}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_coldkey_ban_admin)],
)
async def delete_banned_coldkey(miner_coldkey: str) -> Response:
    validate_coldkey(miner_coldkey)
    await unban_coldkey(miner_coldkey)
    clear_all_ttl_caches()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/validator-sessions/{validator_hotkey}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_coldkey_ban_admin)],
)
async def delete_validator_session(validator_hotkey: str) -> Response:
    registration_lock = validator_endpoint.get_session_registration_lock(validator_hotkey)
    async with DebugLock(registration_lock, f"delete_validator_session() for {validator_hotkey}"):
        session_id = validator_endpoint.is_validator_registered(validator_hotkey)
        validator = validator_endpoint.SESSION_ID_TO_VALIDATOR.get(session_id) if session_id else None
        if validator is None:
            raise HTTPException(status_code=404, detail="No connected validator with the given hotkey")

        async with DebugLock(validator._lock, f"delete_validator_session() for {validator.name}'s lock"):
            if validator.session_id in validator_endpoint.SESSION_ID_TO_VALIDATOR:
                await validator_endpoint.delete_validator(
                    validator, "The validator was kicked by an admin to force a restart."
                )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


class BlacklistedValidatorsResponse(BaseModel):
    blacklisted_validators: list[str]


class ValidatorsPausedResponse(BaseModel):
    validators_paused: bool


@router.put(
    "/blacklisted-validators/{validator_hotkey}",
    dependencies=[Depends(require_coldkey_ban_admin)],
)
async def put_blacklisted_validator(validator_hotkey: str) -> BlacklistedValidatorsResponse:
    blacklist = await add_hotkey_to_blacklist(validator_hotkey)
    return BlacklistedValidatorsResponse(blacklisted_validators=blacklist)


@router.delete(
    "/blacklisted-validators/{validator_hotkey}",
    dependencies=[Depends(require_coldkey_ban_admin)],
)
async def delete_blacklisted_validator(validator_hotkey: str) -> BlacklistedValidatorsResponse:
    blacklist = await remove_hotkey_from_blacklist(validator_hotkey)
    return BlacklistedValidatorsResponse(blacklisted_validators=blacklist)

