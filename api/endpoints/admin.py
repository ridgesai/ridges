import logging
import re
from typing import Annotated, Optional
from uuid import UUID, uuid4

from bittensor_wallet.keypair import Keypair
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile, status
from pydantic import AwareDatetime, BaseModel, StringConstraints

from api.admin_auth import COMPETITION_ADMIN_ACTOR as COMPETITION_ADMIN_ACTOR
from api.admin_auth import require_coldkey_ban_admin
from api.endpoints import validator as validator_endpoint
from api.endpoints.posts import to_public_posts
from db.models import InternalFlagName
from models.banned_coldkey import BannedColdkey
from models.benchmark_phase import BenchmarkPhaseEndRequest, BenchmarkPhaseSnapshot, BenchmarkPhaseStartRequest
from models.competition import (
    CompetitionAdminSnapshot,
    CompetitionAllocationSnapshot,
    CompetitionAllocationUpdateRequest,
    CompetitionMetadataUpdateRequest,
    CompetitionPolicyUpdateRequest,
    CompetitionStateUpdateRequest,
    CompetitionValidatorConcurrencySnapshot,
    UploadPricingSnapshot,
    UploadPricingUpdateRequest,
    ValidatorConcurrencyDeleteRequest,
    ValidatorConcurrencySnapshot,
    ValidatorConcurrencyUpdateRequest,
)
from models.post import Post
from models.upload_credit import UploadCredit
from models.validator_scheduling import (
    CompetitionSchedulingSnapshot,
    CompetitionSchedulingUpdateRequest,
    ValidatorAllowlistSnapshot,
    ValidatorAllowlistUpdateRequest,
)
from queries import post as post_queries
from queries.banned_coldkey import ban_coldkey, unban_coldkey
from queries.benchmark_phase import end_benchmark_phase, get_benchmark_phase, start_benchmark_phase
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
from queries.upload_price import update_competition_pricing
from queries.validator_scheduling import (
    get_competition_scheduling,
    get_validator_allowlist,
    set_competition_scheduling,
    set_validator_allowlist,
)
from utils import s3
from utils.debug_lock import DebugLock
from utils.images import (
    IMAGE_EXTENSIONS,
    ImageTooLargeError,
    InvalidImageError,
    UnsupportedImageError,
    read_image,
)
from utils.ttl import clear_all_ttl_caches
from utils.upload_pricing import multiplier
from utils.validator_hotkeys import PLATFORM_VALIDATOR_PREFIX, is_validator_hotkey_whitelisted

logger = logging.getLogger(__name__)

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


def validate_validator_hotkey(hotkey: str, *, require_whitelisted: bool = False) -> None:
    if re.fullmatch(rf"{PLATFORM_VALIDATOR_PREFIX}\d+", hotkey):
        return

    validate_hotkey(hotkey)
    if require_whitelisted and not is_validator_hotkey_whitelisted(hotkey):
        raise HTTPException(status_code=400, detail="Validator hotkey is not whitelisted")


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


@router.put("/competitions/{set_id}/upload-pricing", response_model=UploadPricingSnapshot)
async def put_competition_upload_pricing(
    set_id: int,
    request: UploadPricingUpdateRequest,
    actor: Annotated[str, Depends(require_coldkey_ban_admin)],
) -> UploadPricingSnapshot:
    settings = request.settings()
    price = await update_competition_pricing(set_id, settings)
    if price is None:
        raise HTTPException(status_code=404, detail=f"Competition {set_id} not found")
    logger.info(f"Upload pricing for competition {set_id} set to {settings} by {actor}: {request.reason}")
    return UploadPricingSnapshot(
        set_id=set_id,
        floor_usd=settings.floor_usd,
        target_per_hour=settings.target_per_hour,
        half_life_minutes=settings.half_life_minutes,
        multiplier=multiplier(settings),
        price_usd=price.price_usd,
        price_updated_at=price.price_updated_at,
        as_of=price.as_of,
    )


@router.get(
    "/competitions/{set_id}/benchmark-phase",
    response_model=BenchmarkPhaseSnapshot,
    dependencies=[Depends(require_coldkey_ban_admin)],
)
async def get_competition_benchmark_phase(set_id: int) -> BenchmarkPhaseSnapshot:
    phase = await get_benchmark_phase(set_id)
    if phase is None:
        raise HTTPException(status_code=404, detail="no_benchmark_phase")
    return phase


@router.post("/competitions/{set_id}/benchmark-phase/start", response_model=BenchmarkPhaseSnapshot)
async def post_competition_benchmark_phase_start(
    set_id: int,
    request: BenchmarkPhaseStartRequest,
    actor: Annotated[str, Depends(require_coldkey_ban_admin)],
) -> BenchmarkPhaseSnapshot:
    phase = await start_benchmark_phase(set_id=set_id, actor=actor, reason=request.reason)
    clear_all_ttl_caches()
    return phase


@router.post("/competitions/{set_id}/benchmark-phase/end", response_model=BenchmarkPhaseSnapshot)
async def post_competition_benchmark_phase_end(
    set_id: int,
    request: BenchmarkPhaseEndRequest,
    actor: Annotated[str, Depends(require_coldkey_ban_admin)],
) -> BenchmarkPhaseSnapshot:
    phase = await end_benchmark_phase(
        set_id=set_id, winner_agent_id=request.winner_agent_id, actor=actor, reason=request.reason
    )
    clear_all_ttl_caches()
    return phase


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
    validate_validator_hotkey(validator_hotkey, require_whitelisted=True)
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
    validate_validator_hotkey(validator_hotkey)
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


@router.get(
    "/competitions/{set_id}/validator-scheduling",
    response_model=CompetitionSchedulingSnapshot,
    dependencies=[Depends(require_coldkey_ban_admin)],
)
async def get_validator_scheduling(set_id: int) -> CompetitionSchedulingSnapshot:
    return await get_competition_scheduling(set_id=set_id)


@router.put("/competitions/{set_id}/validator-scheduling", response_model=CompetitionSchedulingSnapshot)
async def put_validator_scheduling(
    set_id: int,
    request: CompetitionSchedulingUpdateRequest,
    actor: Annotated[str, Depends(require_coldkey_ban_admin)],
) -> CompetitionSchedulingSnapshot:
    return await set_competition_scheduling(set_id=set_id, target=request, actor=actor)


@router.get(
    "/validators/{validator_hotkey}/competition-allowlist",
    response_model=ValidatorAllowlistSnapshot,
    dependencies=[Depends(require_coldkey_ban_admin)],
)
async def get_validator_competition_allowlist(validator_hotkey: str) -> ValidatorAllowlistSnapshot:
    validate_validator_hotkey(validator_hotkey)
    return await get_validator_allowlist(validator_hotkey=validator_hotkey)


@router.put("/validators/{validator_hotkey}/competition-allowlist", response_model=ValidatorAllowlistSnapshot)
async def put_validator_competition_allowlist(
    validator_hotkey: str,
    request: ValidatorAllowlistUpdateRequest,
    actor: Annotated[str, Depends(require_coldkey_ban_admin)],
) -> ValidatorAllowlistSnapshot:
    validate_validator_hotkey(validator_hotkey, require_whitelisted=request.allowed_set_ids is not None)
    return await set_validator_allowlist(validator_hotkey=validator_hotkey, target=request, actor=actor)


# --- Posts (CMS) ----------------------------------------------------------------
#
# Images are stored unmodified in S3 under `posts/`. Every upload gets a fresh key, and an image that
# is no longer referenced is deleted only after the database change succeeds (an orphaned S3 object
# is harmless, a row pointing at a missing object is not).

MAX_POST_IMAGE_SIZE_BYTES = 20 * 1024 * 1024

PostTitle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
PostBody = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50_000)]
PostThumbnailId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


def _require_image_or_thumbnail(*, has_image: bool, unique_thumbnail_id: Optional[str]) -> None:
    if not has_image and unique_thumbnail_id is None:
        raise HTTPException(status_code=422, detail="A post needs an image, a unique_thumbnail_id, or both")


async def _store_post_image(post_id: UUID, image: UploadFile) -> tuple[str, str]:
    """Validate an image and upload it under a fresh key. Returns (s3_key, content_type)."""
    try:
        data, content_type = await read_image(image, max_bytes=MAX_POST_IMAGE_SIZE_BYTES)
    except ImageTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from None
    except UnsupportedImageError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from None
    except InvalidImageError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    # A fresh key per upload, so a replaced image is never served from a stale cache.
    key = f"posts/{post_id}/{uuid4()}.{IMAGE_EXTENSIONS[content_type]}"
    await s3.upload_bytes_to_s3(key, data, content_type=content_type)
    return key, content_type


async def _delete_post_image_best_effort(key: str) -> None:
    """Delete a post image, logging instead of raising: an orphaned object is harmless."""
    try:
        await s3.delete_file_from_s3(key)
    except Exception as exc:
        logger.error(f"Failed to delete post image s3_key={key}: {type(exc).__name__}: {exc}")


@router.post(
    "/posts",
    response_model=Post,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_coldkey_ban_admin)],
)
async def post_post(
    title: Annotated[PostTitle, Form()],
    body: Annotated[PostBody, Form()],
    featured: Annotated[bool, Form()] = False,
    unique_thumbnail_id: Annotated[Optional[PostThumbnailId], Form()] = None,
    image: Annotated[Optional[UploadFile], File()] = None,
) -> Post:
    """Create a post. Requires an `image`, a `unique_thumbnail_id`, or both."""
    _require_image_or_thumbnail(has_image=image is not None, unique_thumbnail_id=unique_thumbnail_id)

    post_id = uuid4()
    image_key, image_content_type = (None, None) if image is None else await _store_post_image(post_id, image)

    try:
        record = await post_queries.create_post(
            post_id=post_id,
            title=title,
            body=body,
            image_s3_key=image_key,
            image_content_type=image_content_type,
            unique_thumbnail_id=unique_thumbnail_id,
            featured=featured,
        )
    except Exception:
        if image_key is not None:
            await _delete_post_image_best_effort(image_key)
        raise

    logger.info(f"Created post {record.id} (featured={record.featured}, has_image={image_key is not None})")
    return (await to_public_posts([record]))[0]


@router.patch("/posts/{post_id}", response_model=Post, dependencies=[Depends(require_coldkey_ban_admin)])
async def patch_post(
    post_id: UUID,
    request: Request,
    title: Annotated[Optional[PostTitle], Form()] = None,
    body: Annotated[Optional[PostBody], Form()] = None,
    featured: Annotated[Optional[bool], Form()] = None,
    unique_thumbnail_id: Annotated[Optional[PostThumbnailId], Form()] = None,
    image: Annotated[Optional[UploadFile], File()] = None,
) -> Post:
    """Update a post; only the fields present in the form change.

    - `featured=true|false` sets the featured flag.
    - `image` with a file replaces the current image.
    - Form data has no null, so sending `image` or `unique_thumbnail_id` with an empty value clears it;
      omitting the field leaves it unchanged.
    """
    # FastAPI turns an empty form value into the parameter default, which makes "sent empty" look
    # like "not sent". The raw form (already parsed and cached by FastAPI) tells the two apart.
    form = await request.form()
    remove_image = image is None and form.get("image") == ""

    existing = await post_queries.get_post(post_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Post not found")

    if "unique_thumbnail_id" not in form:
        unique_thumbnail_id = existing.unique_thumbnail_id
    keeps_existing_image = existing.image_s3_key is not None and not remove_image
    _require_image_or_thumbnail(
        has_image=image is not None or keeps_existing_image,
        unique_thumbnail_id=unique_thumbnail_id,
    )

    image_key = None if remove_image else existing.image_s3_key
    image_content_type = None if remove_image else existing.image_content_type
    uploaded_key: Optional[str] = None
    if image is not None:
        image_key, image_content_type = await _store_post_image(post_id, image)
        uploaded_key = image_key

    try:
        record = await post_queries.update_post(
            post_id,
            title=existing.title if title is None else title,
            body=existing.body if body is None else body,
            image_s3_key=image_key,
            image_content_type=image_content_type,
            unique_thumbnail_id=unique_thumbnail_id,
            featured=existing.featured if featured is None else featured,
        )
    except Exception:
        if uploaded_key is not None:
            await _delete_post_image_best_effort(uploaded_key)
        raise

    if record is None:
        # Deleted between the read and the update.
        if uploaded_key is not None:
            await _delete_post_image_best_effort(uploaded_key)
        raise HTTPException(status_code=404, detail="Post not found")

    if existing.image_s3_key and existing.image_s3_key != record.image_s3_key:
        await _delete_post_image_best_effort(existing.image_s3_key)

    logger.info(f"Updated post {record.id} (featured={record.featured})")
    return (await to_public_posts([record]))[0]


@router.delete(
    "/posts/{post_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_coldkey_ban_admin)],
)
async def delete_post(post_id: UUID) -> Response:
    """Delete a post and its image."""
    record = await post_queries.delete_post(post_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Post not found")
    if record.image_s3_key:
        await _delete_post_image_best_effort(record.image_s3_key)

    logger.info(f"Deleted post {post_id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
