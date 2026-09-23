from typing import Annotated

from bittensor_wallet.keypair import Keypair
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import AwareDatetime, BaseModel, StringConstraints

from api.admin_auth import COMPETITION_ADMIN_ACTOR as COMPETITION_ADMIN_ACTOR
from api.admin_auth import require_coldkey_ban_admin
from api.endpoints import validator as validator_endpoint
from db.models import InternalFlagName
from models.banned_coldkey import BannedColdkey
from models.competition import (
    CompetitionAdminSnapshot,
    CompetitionAllocationSnapshot,
    CompetitionAllocationUpdateRequest,
    CompetitionMetadataUpdateRequest,
    CompetitionPolicyUpdateRequest,
    CompetitionStateUpdateRequest,
    CompetitionValidatorConcurrencySnapshot,
    ValidatorConcurrencyDeleteRequest,
    ValidatorConcurrencySnapshot,
    ValidatorConcurrencyUpdateRequest,
)
from models.upload_credit import UploadCredit
from queries.banned_coldkey import ban_coldkey, unban_coldkey
from queries.competition import (
    get_competition_validator_concurrency,
    replace_competition_allocations,
    replace_competition_metadata,
    replace_competition_policy,
    set_competition_validator_concurrency,
    update_competition_state,
)
from queries.internal_flag import add_hotkey_to_blacklist, remove_hotkey_from_blacklist, set_internal_flag
from queries.upload_credit import grant_upload_credit
from utils.debug_lock import DebugLock
from utils.ttl import clear_all_ttl_caches
from utils.validator_hotkeys import is_validator_hotkey_whitelisted

router = APIRouter(tags=["admin"])


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


@router.put("/competitions/{set_id}", response_model=CompetitionAdminSnapshot)
async def put_competition_metadata(
    set_id: int,
    request: CompetitionMetadataUpdateRequest,
    actor: Annotated[str, Depends(require_coldkey_ban_admin)],
) -> CompetitionAdminSnapshot:
    snapshot = await replace_competition_metadata(set_id=set_id, target=request, actor=actor)
    clear_all_ttl_caches()
    return snapshot


@router.put("/competitions/{set_id}/state", response_model=CompetitionAdminSnapshot)
async def put_competition_state(
    set_id: int,
    request: CompetitionStateUpdateRequest,
    actor: Annotated[str, Depends(require_coldkey_ban_admin)],
) -> CompetitionAdminSnapshot:
    snapshot = await update_competition_state(set_id=set_id, target=request, actor=actor)
    clear_all_ttl_caches()
    return snapshot


@router.put("/competitions/{set_id}/policy", response_model=CompetitionAdminSnapshot)
async def put_competition_policy(
    set_id: int,
    request: CompetitionPolicyUpdateRequest,
    actor: Annotated[str, Depends(require_coldkey_ban_admin)],
) -> CompetitionAdminSnapshot:
    snapshot = await replace_competition_policy(set_id=set_id, target=request, actor=actor)
    clear_all_ttl_caches()
    return snapshot


@router.get(
    "/competitions/{set_id}/validator-concurrency",
    response_model=CompetitionValidatorConcurrencySnapshot,
    dependencies=[Depends(require_coldkey_ban_admin)],
)
async def get_validator_concurrency(set_id: int) -> CompetitionValidatorConcurrencySnapshot:
    return await get_competition_validator_concurrency(set_id=set_id)


@router.put(
    "/competitions/{set_id}/validator-concurrency/{validator_hotkey}", response_model=ValidatorConcurrencySnapshot
)
async def put_validator_concurrency(
    set_id: int,
    validator_hotkey: str,
    request: ValidatorConcurrencyUpdateRequest,
    actor: Annotated[str, Depends(require_coldkey_ban_admin)],
) -> ValidatorConcurrencySnapshot:
    validate_hotkey(validator_hotkey)
    if not is_validator_hotkey_whitelisted(validator_hotkey):
        raise HTTPException(status_code=400, detail="Validator hotkey is not whitelisted")
    return await set_competition_validator_concurrency(
        set_id=set_id,
        validator_hotkey=validator_hotkey,
        max_concurrent_evaluation_runs=request.max_concurrent_evaluation_runs,
        reason=request.reason,
        actor=actor,
    )


@router.delete(
    "/competitions/{set_id}/validator-concurrency/{validator_hotkey}", response_model=ValidatorConcurrencySnapshot
)
async def delete_validator_concurrency(
    set_id: int,
    validator_hotkey: str,
    request: ValidatorConcurrencyDeleteRequest,
    actor: Annotated[str, Depends(require_coldkey_ban_admin)],
) -> ValidatorConcurrencySnapshot:
    validate_hotkey(validator_hotkey)
    return await set_competition_validator_concurrency(
        set_id=set_id,
        validator_hotkey=validator_hotkey,
        max_concurrent_evaluation_runs=None,
        reason=request.reason,
        actor=actor,
    )


@router.put("/competition-allocations", response_model=CompetitionAllocationSnapshot)
async def put_competition_allocations(
    request: CompetitionAllocationUpdateRequest,
    actor: Annotated[str, Depends(require_coldkey_ban_admin)],
) -> CompetitionAllocationSnapshot:
    snapshot = await replace_competition_allocations(target=request, actor=actor)
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
