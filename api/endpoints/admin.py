import secrets
from typing import Annotated, NoReturn

from bittensor_wallet.keypair import Keypair
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import AwareDatetime, BaseModel, StringConstraints

import api.config as config
from api.endpoints import validator as validator_endpoint
from db.models import InternalFlagName
from models.banned_coldkey import BannedColdkey
from models.competition import (
    CompetitionAdminSnapshot,
    CompetitionAllocationSnapshot,
    CompetitionAllocationUpdateRequest,
    CompetitionPolicyUpdateRequest,
    CompetitionStateUpdateRequest,
)
from models.upload_credit import UploadCredit
from queries.banned_coldkey import ban_coldkey, unban_coldkey
from queries.competition import (
    replace_competition_allocations,
    replace_competition_policy,
    update_competition_state,
)
from queries.errors import CompetitionAdminConflictError, CompetitionNotFoundError
from queries.internal_flag import add_hotkey_to_blacklist, remove_hotkey_from_blacklist, set_internal_flag
from queries.upload_credit import grant_upload_credit
from utils.debug_lock import DebugLock
from utils.ttl import clear_all_ttl_caches

router = APIRouter(tags=["admin"])
admin_bearer = HTTPBearer(auto_error=False)
COMPETITION_ADMIN_ACTOR = "coldkey-ban-admin-api-key"


class ColdkeyBanRequest(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class UploadCreditGrantRequest(BaseModel):
    miner_hotkey: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    granted_by: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    grant_reference: Annotated[
        str | None,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
    ] = None
    expires_at: AwareDatetime | None = None


def require_coldkey_ban_admin(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(admin_bearer)],
) -> str:
    expected = config.COLDKEY_BAN_ADMIN_API_KEY
    if not expected:
        raise HTTPException(status_code=503, detail="Admin API is not configured")
    if credentials is None or not secrets.compare_digest(credentials.credentials, expected):
        raise HTTPException(
            status_code=401,
            detail="Invalid admin credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return COMPETITION_ADMIN_ACTOR


def validate_coldkey(miner_coldkey: str) -> None:
    try:
        Keypair(ss58_address=miner_coldkey)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid coldkey SS58 address") from None


def validate_hotkey(miner_hotkey: str) -> None:
    try:
        Keypair(ss58_address=miner_hotkey)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid hotkey SS58 address") from None


def _raise_competition_admin_error(error: Exception) -> NoReturn:
    if isinstance(error, CompetitionNotFoundError):
        raise HTTPException(status_code=404, detail=str(error)) from error

    if isinstance(error, CompetitionAdminConflictError):
        raise HTTPException(status_code=409, detail=str(error)) from error

    raise error


@router.put("/competitions/{set_id}/state", response_model=CompetitionAdminSnapshot)
async def put_competition_state(
    set_id: int,
    request: CompetitionStateUpdateRequest,
    actor: Annotated[str, Depends(require_coldkey_ban_admin)],
) -> CompetitionAdminSnapshot:
    try:
        snapshot = await update_competition_state(set_id=set_id, target=request, actor=actor)
    except (CompetitionNotFoundError, CompetitionAdminConflictError) as error:
        _raise_competition_admin_error(error)
    clear_all_ttl_caches()
    return snapshot


@router.put("/competitions/{set_id}/policy", response_model=CompetitionAdminSnapshot)
async def put_competition_policy(
    set_id: int,
    request: CompetitionPolicyUpdateRequest,
    actor: Annotated[str, Depends(require_coldkey_ban_admin)],
) -> CompetitionAdminSnapshot:
    try:
        snapshot = await replace_competition_policy(set_id=set_id, target=request, actor=actor)
    except (CompetitionNotFoundError, CompetitionAdminConflictError) as error:
        _raise_competition_admin_error(error)
    clear_all_ttl_caches()
    return snapshot


@router.put("/competition-allocations", response_model=CompetitionAllocationSnapshot)
async def put_competition_allocations(
    request: CompetitionAllocationUpdateRequest,
    actor: Annotated[str, Depends(require_coldkey_ban_admin)],
) -> CompetitionAllocationSnapshot:
    try:
        snapshot = await replace_competition_allocations(target=request, actor=actor)
    except CompetitionAdminConflictError as error:
        _raise_competition_admin_error(error)
    clear_all_ttl_caches()
    return snapshot


@router.post(
    "/upload-credits",
    response_model=UploadCredit,
    dependencies=[Depends(require_coldkey_ban_admin)],
)
async def post_upload_credit(request: UploadCreditGrantRequest) -> UploadCredit:
    validate_hotkey(request.miner_hotkey)
    credit = await grant_upload_credit(
        miner_hotkey=request.miner_hotkey,
        reason=request.reason,
        granted_by=request.granted_by,
        grant_reference=request.grant_reference,
        expires_at=request.expires_at,
    )
    if credit is None:
        raise HTTPException(status_code=400, detail="Upload credit expiry must be in the future")
    return credit


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


@router.put(
    "/validators-paused",
    dependencies=[Depends(require_coldkey_ban_admin)],
)
async def put_validators_paused() -> ValidatorsPausedResponse:
    await set_internal_flag(InternalFlagName.VALIDATORS_PAUSED, "true")
    return ValidatorsPausedResponse(validators_paused=True)


@router.delete(
    "/validators-paused",
    dependencies=[Depends(require_coldkey_ban_admin)],
)
async def delete_validators_paused() -> ValidatorsPausedResponse:
    await set_internal_flag(InternalFlagName.VALIDATORS_PAUSED, "false")
    return ValidatorsPausedResponse(validators_paused=False)
