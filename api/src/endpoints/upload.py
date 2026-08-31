import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from api import config
from api.errors import PaymentAlreadyUsedError, PaymentRefunded, PlatformFrozenError
from api.src.utils.openrouter_validation import validate_openrouter_keys
from api.src.utils.request_cache import hourly_cache
from api.src.utils.upload_agent_helpers import (
    as_utc,
    check_coldkey_banned,
    check_file_size,
    check_hotkey_registered,
    check_if_extrinsic_failed,
    check_if_python_file,
    check_rate_limit,
    check_signature,
    find_alpha_burned_event,
    get_alpha_price,
    get_miner_hotkey,
    timestamp_ms_to_utc_datetime,
    verify_burn_extrinsic,
)
from models.agent import AgentCreate
from models.upload import (
    AgentCheckResponse,
    AgentDirectCheckResponse,
    AgentUploadResponse,
    ErrorResponse,
    OpenRouterKeysCheckRequest,
    OpenRouterKeysCheckResponse,
    PrepareUploadRequest,
    TicketCheckRequest,
    TicketCheckResponse,
    UploadPriceResponse,
)
from queries.agent import (
    BurnUploadFunding,
    CreditUploadFunding,
    _derive_agent_id,
    admit_agent,
    get_latest_agent_created_at_for_miner_hotkey_in_competition,
    record_upload_attempt,
)
from queries.banned_coldkey import get_banned_coldkey
from queries.competition import resolve_upload_competition
from queries.errors import (
    ColdkeyBannedError,
    CompetitionNotAcceptingSubmissionsError,
    DuplicateAgentIDError,
    UploadCooldownError,
    UploadCreditAlreadyRedeemedError,
    UploadCreditUnavailableError,
    UploadFundingConflictError,
)
from queries.payments import (
    create_payment_quote,
    retrieve_payment_by_hash,
    retrieve_payment_quote,
)
from queries.refund import is_payment_refunded
from queries.upload_credit import get_exact_upload_credit_replay, get_upload_credit_by_id, get_upload_credit_for_check
from utils.agent_secrets import encrypt_agent_secret
from utils.bittensor import subtensor_client
from utils.s3 import upload_text_file_to_s3
from utils.upload_ticket import (
    FUNDING_BURN,
    FUNDING_CREDIT,
    decode_ticket,
    prepare_signing_string,
    verify_ticket_signature,
)

logger = logging.getLogger(__name__)

UPLOAD_PAYMENT_QUOTE_TTL_SECONDS = 60 * 60
OUTDATED_UPLOAD_CLIENT_MESSAGE = "This upload client is outdated. Please upgrade Ridges CLI and retry."
COMPETITION_SELECTION_REQUIRED_MESSAGE = (
    "No competition was selected. Choose a competition (set_id) and retry; CLI users should upgrade Ridges CLI."
)

router = APIRouter()


async def _resolve_upload_set_id(set_id: int) -> int:
    try:
        return await resolve_upload_competition(set_id)
    except CompetitionNotAcceptingSubmissionsError as exception:
        raise HTTPException(status_code=409, detail=str(exception)) from exception


async def _exact_credit_replay_response(
    *,
    credit_id: UUID,
    miner_hotkey: str,
    source_sha256: str,
    set_id: int,
    upload_data: dict,
) -> AgentUploadResponse | None:
    try:
        replayed_agent_id = await get_exact_upload_credit_replay(
            credit_id=credit_id,
            miner_hotkey=miner_hotkey,
            source_sha256=source_sha256,
            set_id=set_id,
        )
    except UploadCreditAlreadyRedeemedError as exception:
        raise HTTPException(
            status_code=409,
            detail=f"Upload credit {credit_id} was already used for agent {exception.agent_id}",
        ) from exception

    if replayed_agent_id is None:
        return None

    success_message = (
        f"Upload credit {credit_id} was already used for agent {replayed_agent_id}. No new agent was created."
    )
    await record_upload_attempt(
        upload_type="agent",
        success=True,
        agent_id=replayed_agent_id,
        **upload_data,
    )
    return AgentUploadResponse(status="success", message=success_message)


@router.post("/agent/check", tags=["upload"], response_model=AgentDirectCheckResponse)
async def check_agent_post(
    request: Request,
    agent_file: UploadFile = File(..., description="Python file containing the agent code (must be named agent.py)"),
    public_key: str = Form(..., description="Public key of the miner in hex format"),
    file_info: str = Form(
        ..., description="File information containing miner hotkey and version number (format: hotkey:version)"
    ),
    signature: str = Form(..., description="Signature to verify the authenticity of the upload"),
    name: str = Form(..., description="Name of the agent"),
    openrouter_api_key: str = Form(..., description="OpenRouter API key for inference during evaluation"),
    openrouter_management_key: str = Form(
        ..., description="OpenRouter management key used to validate workspace privacy settings"
    ),
    use_credit: Annotated[bool, Form(description="Use a upload credit instead of burning alpha")] = False,
    credit_id: Annotated[Optional[str], Form(description="Specific upload credit ID for a retry")] = None,
    set_id: Annotated[Optional[int], Form(description="Competition to enter")] = None,
) -> AgentDirectCheckResponse:
    if config.DISALLOW_UPLOADS:
        raise HTTPException(status_code=503, detail=config.DISALLOW_UPLOADS_REASON)
    if set_id is None:
        raise HTTPException(status_code=400, detail=OUTDATED_UPLOAD_CLIENT_MESSAGE)
    miner_hotkey = get_miner_hotkey(file_info)
    is_owner_upload = miner_hotkey == config.OWNER_HOTKEY
    resolved_set_id = await _resolve_upload_set_id(set_id)
    if credit_id is not None and not use_credit:
        raise HTTPException(status_code=400, detail="credit_id requires use_credit")
    if use_credit and (config.ENV != "prod" or is_owner_upload):
        raise HTTPException(status_code=400, detail="Upload credits are only available for production miner uploads")
    if config.ENV == "prod" and not use_credit and not is_owner_upload:
        latest_agent_created_at = await get_latest_agent_created_at_for_miner_hotkey_in_competition(
            miner_hotkey=miner_hotkey,
            set_id=resolved_set_id,
        )
        if latest_agent_created_at:
            check_rate_limit(latest_agent_created_at)
    check_signature(public_key, file_info, signature, miner_hotkey)
    await check_hotkey_registered(miner_hotkey)
    coldkey = await subtensor_client.get_hotkey_owner(miner_hotkey)
    if coldkey is None:
        raise HTTPException(status_code=400, detail="Hotkey owner not found")
    if not is_owner_upload:
        await check_coldkey_banned(coldkey)
    check_if_python_file(agent_file.filename)
    await check_file_size(agent_file)

    if use_credit:
        requested_credit_id: Optional[UUID] = None
        if credit_id is not None:
            try:
                requested_credit_id = UUID(credit_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid upload credit ID") from None

        credit = await get_upload_credit_for_check(
            miner_hotkey=miner_hotkey,
            credit_id=requested_credit_id,
        )
        if credit is None:
            raise HTTPException(status_code=402, detail="No usable upload credit is available for this hotkey")

        await validate_openrouter_keys(
            openrouter_api_key=openrouter_api_key,
            openrouter_management_key=openrouter_management_key,
        )
        return AgentDirectCheckResponse(
            status="success",
            message="Agent check successful",
            payment_method="credit",
            credit_id=credit.credit_id,
            amount_alpha_rao=0,
            set_id=resolved_set_id,
        )

    try:
        alpha_stake = await subtensor_client.get_alpha_stake_availability(
            coldkey=coldkey,
            hotkey=miner_hotkey,
            netuid=config.NETUID,
        )
    except Exception as e:
        logger.error(f"Error retrieving burnable alpha stake: {e}")
        raise HTTPException(status_code=503, detail="Burnable alpha stake could not be verified") from e

    payment_cost = await get_upload_price()
    if payment_cost.amount_alpha_rao > alpha_stake.burnable_rao:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Insufficient alpha. You need {payment_cost.amount_alpha_rao} rao "
                f"burnable from the miner hotkey position on SN{config.NETUID}. "
                f"Position: {alpha_stake.position_rao}; subnet total: {alpha_stake.total_rao}; "
                f"locked: {alpha_stake.locked_rao}; burnable: {alpha_stake.burnable_rao}."
            ),
        )
    await validate_openrouter_keys(
        openrouter_api_key=openrouter_api_key,
        openrouter_management_key=openrouter_management_key,
    )
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=UPLOAD_PAYMENT_QUOTE_TTL_SECONDS)
    quote = await create_payment_quote(
        miner_hotkey=miner_hotkey,
        amount_alpha_rao=payment_cost.amount_alpha_rao,
        expires_at=expires_at,
    )
    return AgentDirectCheckResponse(
        status="success",
        message="Agent check successful",
        payment_method="burn",
        quote_id=quote.quote_id,
        amount_alpha_rao=quote.amount_alpha_rao,
        payment_netuid=config.NETUID,
        expires_at=quote.expires_at,
        set_id=resolved_set_id,
    )


@router.post("/prepare", tags=["upload"], response_model=AgentCheckResponse)
async def prepare_upload(body: PrepareUploadRequest) -> AgentCheckResponse:
    """Reserve funding for a web-upload ticket: a burn quote (default) or an admin-granted upload credit.

    Takes no agent file and no OpenRouter keys. Both are provided at redeem time on the web.
    """
    if config.DISALLOW_UPLOADS:
        raise HTTPException(status_code=503, detail=config.DISALLOW_UPLOADS_REASON)

    try:
        check_signature(body.public_key, prepare_signing_string(body.hotkey), body.signature, body.hotkey)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed public key or signature") from None

    is_owner_upload = body.hotkey == config.OWNER_HOTKEY
    if is_owner_upload:
        raise HTTPException(status_code=400, detail="Owner uploads use team-upload, not tickets")

    if body.credit_id is not None and not body.use_credit:
        raise HTTPException(status_code=400, detail="credit_id requires use_credit")

    if body.use_credit and (config.ENV != "prod" or is_owner_upload):
        raise HTTPException(status_code=400, detail="Upload credits are only available for production miner uploads")

    await check_hotkey_registered(body.hotkey)
    coldkey = await subtensor_client.get_hotkey_owner(body.hotkey)
    if coldkey is None:
        raise HTTPException(status_code=400, detail="Hotkey owner not found")

    if not is_owner_upload:
        await check_coldkey_banned(coldkey)

    if body.use_credit:
        credit = await get_upload_credit_for_check(miner_hotkey=body.hotkey, credit_id=body.credit_id)
        if credit is None:
            raise HTTPException(status_code=402, detail="No usable upload credit is available for this hotkey")

        return AgentCheckResponse(
            status="success",
            message="Upload credit available; sign and mint your ticket",
            payment_method="credit",
            credit_id=credit.credit_id,
            amount_alpha_rao=0,
        )

    try:
        alpha_stake = await subtensor_client.get_alpha_stake_availability(
            coldkey=coldkey,
            hotkey=body.hotkey,
            netuid=config.NETUID,
        )
    except Exception as e:
        logger.error(f"Error retrieving burnable alpha stake: {e}")
        raise HTTPException(status_code=503, detail="Burnable alpha stake could not be verified") from e

    payment_cost = await get_upload_price()
    if payment_cost.amount_alpha_rao > alpha_stake.burnable_rao:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Insufficient alpha. You need {payment_cost.amount_alpha_rao} rao "
                f"burnable from the miner hotkey position on SN{config.NETUID}."
            ),
        )

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=UPLOAD_PAYMENT_QUOTE_TTL_SECONDS)
    quote = await create_payment_quote(
        miner_hotkey=body.hotkey,
        amount_alpha_rao=payment_cost.amount_alpha_rao,
        expires_at=expires_at,
    )
    return AgentCheckResponse(
        status="success",
        message="Burn quote issued; pay then sign and mint your ticket",
        payment_method="burn",
        quote_id=quote.quote_id,
        amount_alpha_rao=quote.amount_alpha_rao,
        payment_netuid=config.NETUID,
        expires_at=quote.expires_at,
    )


async def _process_agent_upload(
    request: Request,
    agent_file: UploadFile,
    miner_hotkey: str,
    name: str,
    payment_block_hash: Optional[str],
    payment_extrinsic_index: Optional[str],
    quote_id: Optional[str],
    credit_id: Optional[str],
    openrouter_api_key: str,
    openrouter_management_key: str,
    legacy_signature: Optional[tuple[str, str, str]],
    set_id: int,
) -> AgentUploadResponse:
    """Shared upload core for /upload/agent (legacy file_info signature) and /upload/agent/ticket.

    legacy_signature is (public_key, file_info, signature) for the legacy route; None means the
    caller already verified a ticket signature for miner_hotkey.
    """
    prod = config.ENV == "prod"

    coldkey: Optional[str] = None

    # Extract upload attempt data for tracking
    agent_file.file.seek(0, 2)
    file_size_bytes = agent_file.file.tell()
    agent_file.file.seek(0)

    upload_data = {
        "hotkey": miner_hotkey,
        "agent_name": name,
        "filename": agent_file.filename,
        "file_size_bytes": file_size_bytes,
        "ip_address": getattr(request.client, "host", None) if request.client else None,
    }

    try:
        logger.info(f"Uploading agent {name} for miner {miner_hotkey}.")

        is_owner_upload = miner_hotkey == config.OWNER_HOTKEY
        is_credit_upload = credit_id is not None
        logger.info("Owner upload: " + str(is_owner_upload))

        if is_credit_upload and (not prod or is_owner_upload):
            raise HTTPException(
                status_code=400, detail="Upload credits are only available for production miner uploads"
            )

        if prod and legacy_signature is not None:
            check_signature(legacy_signature[0], legacy_signature[1], legacy_signature[2], miner_hotkey)

        if config.DISALLOW_UPLOADS and not is_owner_upload:
            raise PlatformFrozenError(config.DISALLOW_UPLOADS_REASON)

        if prod:
            await check_hotkey_registered(miner_hotkey)

        check_if_python_file(agent_file.filename)
        agent_bytes, agent_text = await check_file_size(agent_file)
        source_sha256 = hashlib.sha256(agent_bytes).hexdigest()

        credit_uuid: Optional[UUID] = None
        resolved_set_id: int | None = None
        if prod and not is_owner_upload and is_credit_upload:
            if any(value is not None for value in (quote_id, payment_block_hash, payment_extrinsic_index)):
                raise HTTPException(status_code=400, detail="Credit uploads cannot include burn payment fields")
            try:
                credit_uuid = UUID(credit_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid upload credit ID") from None

            coldkey = await subtensor_client.get_hotkey_owner(miner_hotkey)
            if coldkey is None:
                raise HTTPException(status_code=400, detail="Hotkey owner not found")
            await check_coldkey_banned(coldkey)

        if not is_credit_upload:
            resolved_set_id = await _resolve_upload_set_id(set_id)

        if prod and not is_owner_upload and not is_credit_upload:
            if quote_id is None:
                raise HTTPException(status_code=400, detail=OUTDATED_UPLOAD_CLIENT_MESSAGE)
            if payment_block_hash is None or payment_extrinsic_index is None:
                raise HTTPException(status_code=400, detail="Burn payment information is required")

            try:
                quote_uuid = UUID(quote_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid payment quote ID") from None

            quote = await retrieve_payment_quote(quote_uuid)
            if quote is None:
                raise HTTPException(status_code=400, detail="Invalid payment quote ID")

            if quote.miner_hotkey != miner_hotkey:
                raise HTTPException(status_code=402, detail="Payment quote does not match upload hotkey")

            if quote.amount_alpha_rao is None:
                raise HTTPException(status_code=400, detail=OUTDATED_UPLOAD_CLIENT_MESSAGE)

            existing_payment = await retrieve_payment_by_hash(
                payment_block_hash=payment_block_hash, payment_extrinsic_index=payment_extrinsic_index
            )
            if existing_payment is not None and existing_payment.agent_id is not None:
                raise DuplicateAgentIDError(agent_id=existing_payment.agent_id)
            if existing_payment is not None and existing_payment.quote_id != quote.quote_id:
                raise HTTPException(status_code=409, detail="Payment is already reserved for a different quote")

            if await is_payment_refunded(
                upload_block_hash=payment_block_hash, upload_extrinsic_index=payment_extrinsic_index
            ):
                logger.warning(f"Payment with block hash {payment_block_hash} has been refunded. Rejecting upload.")
                raise PaymentRefunded()

            # Retrieve the burn block + events from the chain
            try:
                payment_block_info = await subtensor_client.get_block_info(block_hash=payment_block_hash)
            except Exception as e:
                logger.error(f"Error retrieving payment block: {e}")
                raise HTTPException(status_code=402, detail="Payment could not be verified")

            if payment_block_info is None:
                raise HTTPException(status_code=402, detail="Payment block not found")

            try:
                payment_extrinsic_index_int = int(payment_extrinsic_index)
                if payment_extrinsic_index_int < 0:
                    raise ValueError
                payment_extrinsic = payment_block_info.extrinsics[payment_extrinsic_index_int]
            except (ValueError, TypeError, IndexError, AttributeError):
                raise HTTPException(status_code=402, detail="Burn extrinsic could not be decoded") from None

            coldkey = await subtensor_client.get_hotkey_owner(miner_hotkey, block=int(payment_block_info.number))
            if coldkey is None:
                raise HTTPException(status_code=402, detail="Hotkey owner not found at payment block")
            await check_coldkey_banned(coldkey)

            events = await subtensor_client.get_events(block_hash=payment_block_hash)
            if await check_if_extrinsic_failed(payment_extrinsic_index_int, events):
                raise HTTPException(status_code=402, detail="Burn extrinsic failed on-chain")

            # Cross-check the extrinsic: recognized burn call signed by the miner coldkey.
            verify_burn_extrinsic(payment_extrinsic, expected_coldkey=coldkey)

            # Event is the source of truth for amount, netuid, and burner.
            burn_event = find_alpha_burned_event(
                events,
                payment_extrinsic_index_int,
                netuid=config.NETUID,
            )

            if burn_event.coldkey != coldkey:
                raise HTTPException(status_code=402, detail="Coldkey does not match")

            if burn_event.hotkey != miner_hotkey:
                raise HTTPException(status_code=402, detail="Hotkey does not match")

            if burn_event.alpha_decrease < quote.amount_alpha_rao:
                raise HTTPException(status_code=402, detail="Burn amount too low")

            payment_value = burn_event.alpha_decrease

            payment_block_time = timestamp_ms_to_utc_datetime(payment_block_info.timestamp)
            if not (as_utc(quote.created_at) <= payment_block_time <= as_utc(quote.expires_at)):
                raise HTTPException(status_code=402, detail="Payment was made outside the quote validity window")

        validated_openrouter_keys = await validate_openrouter_keys(
            openrouter_api_key=openrouter_api_key,
            openrouter_management_key=openrouter_management_key,
        )

        if credit_uuid is not None:
            replay_response = await _exact_credit_replay_response(
                credit_id=credit_uuid,
                miner_hotkey=miner_hotkey,
                source_sha256=source_sha256,
                set_id=set_id,
                upload_data=upload_data,
            )
            if replay_response is not None:
                return replay_response
            try:
                resolved_set_id = await _resolve_upload_set_id(set_id)
            except HTTPException:
                replay_response = await _exact_credit_replay_response(
                    credit_id=credit_uuid,
                    miner_hotkey=miner_hotkey,
                    source_sha256=source_sha256,
                    set_id=set_id,
                    upload_data=upload_data,
                )
                if replay_response is not None:
                    return replay_response
                raise

        if resolved_set_id is None:
            raise HTTPException(status_code=409, detail="No competition was selected")

        encrypted_openrouter_api_key = encrypt_agent_secret(validated_openrouter_keys.runtime_api_key)
        encrypted_openrouter_management_key = encrypt_agent_secret(validated_openrouter_keys.management_api_key)
        if is_credit_upload:
            agent_payment_block_hash = f"credit:{credit_uuid}"
            agent_payment_extrinsic_index = "0"
        else:
            agent_payment_block_hash = payment_block_hash
            agent_payment_extrinsic_index = payment_extrinsic_index
        if agent_payment_block_hash is None or agent_payment_extrinsic_index is None:
            raise HTTPException(status_code=400, detail="Payment information is required")

        agent = AgentCreate(
            miner_hotkey=miner_hotkey,
            name=name,
            version_num=0,
            created_at=datetime.now(timezone.utc),
            ip_address=request.client.host if request.client else None,
            payment_block_hash=agent_payment_block_hash,
            payment_extrinsic_index=agent_payment_extrinsic_index,
        )
        agent_id = _derive_agent_id(agent_payment_block_hash, agent_payment_extrinsic_index)
        await upload_text_file_to_s3(f"{agent_id}/agent.py", agent_text)

        funding: BurnUploadFunding | CreditUploadFunding | None
        if prod and not is_owner_upload and is_credit_upload:
            funding = CreditUploadFunding(
                credit_id=credit_uuid,
                miner_hotkey=miner_hotkey,
                miner_coldkey=coldkey,
            )
        elif prod and not is_owner_upload:
            funding = BurnUploadFunding(
                payment_block_hash=payment_block_hash,
                payment_extrinsic_index=payment_extrinsic_index,
                miner_hotkey=miner_hotkey,
                miner_coldkey=coldkey,
                amount_alpha_rao=payment_value,
                quote_id=quote.quote_id,
            )
        else:
            funding = None

        try:
            admission = await admit_agent(
                agent,
                set_id=resolved_set_id,
                source_sha256=source_sha256,
                runtime_openrouter_api_key_ciphertext=encrypted_openrouter_api_key,
                management_openrouter_api_key_ciphertext=encrypted_openrouter_management_key,
                openrouter_workspace_id=validated_openrouter_keys.workspace_id,
                openrouter_api_key_label=validated_openrouter_keys.api_key_label,
                openrouter_api_key_creator_user_id=validated_openrouter_keys.api_key_creator_user_id,
                openrouter_validated_at=validated_openrouter_keys.validated_at,
                miner_coldkey=coldkey if prod else None,
                funding=funding,
                enforce_cooldown=prod and not is_owner_upload,
            )
        except ColdkeyBannedError as exception:
            raise HTTPException(status_code=403, detail="Your miner coldkey has been banned") from exception
        except CompetitionNotAcceptingSubmissionsError as exception:
            if credit_uuid is not None:
                replay_response = await _exact_credit_replay_response(
                    credit_id=credit_uuid,
                    miner_hotkey=miner_hotkey,
                    source_sha256=source_sha256,
                    set_id=set_id,
                    upload_data=upload_data,
                )
                if replay_response is not None:
                    return replay_response
            raise HTTPException(status_code=409, detail=str(exception)) from exception

        except UploadCooldownError as exception:
            try:
                check_rate_limit(exception.latest_created_at)
            except HTTPException:
                raise
            raise HTTPException(status_code=429, detail="Upload cooldown has not elapsed") from exception

        except UploadFundingConflictError as exception:
            raise HTTPException(status_code=409, detail="Payment or quote is already reserved") from exception

        except UploadCreditUnavailableError as exception:
            raise HTTPException(status_code=402, detail="Upload credit is not available for this hotkey") from exception

        except UploadCreditAlreadyRedeemedError as exception:
            raise HTTPException(
                status_code=409,
                detail=f"Upload credit {credit_uuid} was already used for agent {exception.agent_id}",
            ) from exception

        agent_id = admission.agent_id
        if admission.replayed:
            success_message = (
                f"Upload credit {credit_uuid} was already used for agent {agent_id}. No new agent was created."
            )
        else:
            success_message = f"Successfully uploaded agent {agent_id} for miner {miner_hotkey}."

        logger.info(success_message)

        # Record successful upload
        await record_upload_attempt(upload_type="agent", success=True, agent_id=agent_id, **upload_data)

        return AgentUploadResponse(status="success", message=success_message)

    except DuplicateAgentIDError as e:
        logger.warning(f"Agent upload failed, duplicate agent ID found: {e}")
        raise PaymentAlreadyUsedError() from e

    except PlatformFrozenError as e:
        logger.warning(f"Upload attempt rejected due to platform freeze: {e}")
        raise

    except HTTPException as e:
        # Determine error type and get ban reason if applicable
        error_type = (
            "banned"
            if e.status_code == 403 and "banned" in e.detail.lower()
            else "rate_limit"
            if e.status_code == 429
            else "validation_error"
        )
        banned_coldkey = await get_banned_coldkey(coldkey) if error_type == "banned" and coldkey else None

        # Record failed upload attempt
        await record_upload_attempt(
            upload_type="agent",
            success=False,
            error_type=error_type,
            error_message=e.detail,
            ban_reason=banned_coldkey.banned_reason if banned_coldkey else None,
            http_status_code=e.status_code,
            **upload_data,
        )
        raise

    except Exception as e:
        # Record internal error
        await record_upload_attempt(
            upload_type="agent",
            success=False,
            error_type="internal_error",
            error_message=str(e),
            http_status_code=500,
            **upload_data,
        )
        raise


@router.post(
    "/agent",
    tags=["upload"],
    response_model=AgentUploadResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Bad Request - Invalid input or validation failed"},
        402: {"model": ErrorResponse, "description": "Payment Required - Payment failed or insufficient funds"},
        409: {"model": ErrorResponse, "description": "Conflict - Upload request already processed"},
        429: {"model": ErrorResponse, "description": "Too Many Requests - Rate limit exceeded"},
        500: {"model": ErrorResponse, "description": "Internal Server Error - Server-side processing failed"},
        503: {"model": ErrorResponse, "description": "Service Unavailable - No screeners available for evaluation"},
    },
)
async def post_agent(
    request: Request,
    agent_file: UploadFile = File(..., description="Python file containing the agent code (must be named agent.py)"),
    public_key: str = Form(..., description="Public key of the miner in hex format"),
    file_info: str = Form(
        ..., description="File information containing miner hotkey and version number (format: hotkey:version)"
    ),
    signature: str = Form(..., description="Signature to verify the authenticity of the upload"),
    name: str = Form(..., description="Name of the agent"),
    payment_block_hash: Optional[str] = Form(None, description="Block hash in which payment was made"),
    payment_extrinsic_index: Optional[str] = Form(None, description="Index in the block for payment extrinsic"),
    quote_id: Optional[str] = Form(None, description="Server-issued upload payment quote ID"),
    credit_id: Annotated[Optional[str], Form(description="One-shot upload credit ID")] = None,
    openrouter_api_key: str = Form(..., description="OpenRouter API key for inference during evaluation"),
    openrouter_management_key: str = Form(
        ..., description="OpenRouter management key used to validate workspace privacy settings"
    ),
    set_id: Annotated[Optional[int], Form(description="Competition to enter")] = None,
) -> AgentUploadResponse:
    """
    Upload a new agent version for evaluation

    This endpoint allows miners to upload their agent code for evaluation. The agent must:
    - Be a Python file
    - Be under 2MB in size
    - Pass static code safety checks
    - Pass similarity validation to prevent copying
    - Be properly signed with the miner's keypair

    Rate limiting may apply based on configuration.
    """
    if set_id is None:
        raise HTTPException(status_code=400, detail=OUTDATED_UPLOAD_CLIENT_MESSAGE)
    return await _process_agent_upload(
        request=request,
        agent_file=agent_file,
        miner_hotkey=get_miner_hotkey(file_info),
        name=name,
        payment_block_hash=payment_block_hash,
        payment_extrinsic_index=payment_extrinsic_index,
        quote_id=quote_id,
        credit_id=credit_id,
        openrouter_api_key=openrouter_api_key,
        openrouter_management_key=openrouter_management_key,
        legacy_signature=(public_key, file_info, signature),
        set_id=set_id,
    )


@router.post(
    "/agent/ticket",
    tags=["upload"],
    response_model=AgentUploadResponse,
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Bad Request - malformed_ticket / invalid_signature / validation failed",
        },
        402: {"model": ErrorResponse, "description": "Payment Required - burn or credit could not be verified"},
        403: {"model": ErrorResponse, "description": "Forbidden - coldkey banned"},
        409: {"model": ErrorResponse, "description": "Conflict - ticket funding already redeemed"},
        429: {"model": ErrorResponse, "description": "Too Many Requests - Rate limit exceeded"},
        500: {"model": ErrorResponse, "description": "Internal Server Error"},
        503: {"model": ErrorResponse, "description": "Service Unavailable"},
    },
)
async def post_agent_ticket(
    request: Request,
    agent_file: UploadFile = File(..., description="Python file containing the agent code (must be named agent.py)"),
    ticket: str = Form(..., description="Upload ticket (ridges1...) minted by `ridges prepare-upload`"),
    name: str = Form(..., description="Name of the agent (used only for a hotkey's first upload)"),
    openrouter_api_key: str = Form(..., description="OpenRouter API key for inference during evaluation"),
    openrouter_management_key: str = Form(
        ..., description="OpenRouter management key used to validate workspace privacy settings"
    ),
    set_id: Annotated[Optional[int], Form(description="Competition to enter")] = None,
) -> AgentUploadResponse:
    """Redeem a prepare-upload ticket: same verification and creation flow as /upload/agent."""
    if set_id is None:
        raise HTTPException(status_code=400, detail=COMPETITION_SELECTION_REQUIRED_MESSAGE)
    try:
        decoded = decode_ticket(ticket)
    except ValueError as exception:
        logger.info(f"Rejected malformed upload ticket: {exception}")
        raise HTTPException(status_code=400, detail="malformed_ticket") from exception
    if not verify_ticket_signature(decoded):
        agent_file.file.seek(0, 2)
        file_size_bytes = agent_file.file.tell()
        agent_file.file.seek(0)
        await record_upload_attempt(
            upload_type="agent",
            success=False,
            error_type="validation_error",
            error_message="invalid_signature",
            http_status_code=400,
            hotkey=decoded.hotkey,
            agent_name=name,
            filename=agent_file.filename,
            file_size_bytes=file_size_bytes,
            ip_address=getattr(request.client, "host", None) if request.client else None,
        )
        raise HTTPException(status_code=400, detail="invalid_signature")

    if decoded.hotkey == config.OWNER_HOTKEY:
        agent_file.file.seek(0, 2)
        file_size_bytes = agent_file.file.tell()
        agent_file.file.seek(0)
        await record_upload_attempt(
            upload_type="agent",
            success=False,
            error_type="validation_error",
            error_message="owner_not_allowed",
            http_status_code=400,
            hotkey=decoded.hotkey,
            agent_name=name,
            filename=agent_file.filename,
            file_size_bytes=file_size_bytes,
            ip_address=getattr(request.client, "host", None) if request.client else None,
        )
        raise HTTPException(status_code=400, detail="owner_not_allowed")

    if decoded.funding == FUNDING_CREDIT:
        return await _process_agent_upload(
            request=request,
            agent_file=agent_file,
            miner_hotkey=decoded.hotkey,
            name=name,
            payment_block_hash=None,
            payment_extrinsic_index=None,
            quote_id=None,
            credit_id=decoded.credit_id,
            openrouter_api_key=openrouter_api_key,
            openrouter_management_key=openrouter_management_key,
            legacy_signature=None,
            set_id=set_id,
        )
    return await _process_agent_upload(
        request=request,
        agent_file=agent_file,
        miner_hotkey=decoded.hotkey,
        name=name,
        payment_block_hash=decoded.payment_block_hash,
        payment_extrinsic_index=str(decoded.payment_extrinsic_index),
        quote_id=decoded.quote_id,
        credit_id=None,
        openrouter_api_key=openrouter_api_key,
        openrouter_management_key=openrouter_management_key,
        legacy_signature=None,
        set_id=set_id,
    )


@router.post("/ticket/check", tags=["upload"], response_model=TicketCheckResponse)
async def check_ticket(body: TicketCheckRequest) -> TicketCheckResponse:
    """Redeemability check for an upload ticket"""
    try:
        ticket = decode_ticket(body.ticket)
    except ValueError:
        return TicketCheckResponse(valid=False, reason="malformed_ticket")

    if not verify_ticket_signature(ticket):
        return TicketCheckResponse(
            valid=False, reason="invalid_signature", hotkey=ticket.hotkey, funding=ticket.funding
        )

    if ticket.hotkey == config.OWNER_HOTKEY:
        return TicketCheckResponse(
            valid=False, reason="owner_not_allowed", hotkey=ticket.hotkey, funding=ticket.funding
        )

    if ticket.funding == FUNDING_BURN:
        quote = await retrieve_payment_quote(UUID(ticket.quote_id))
        if quote is None or quote.miner_hotkey != ticket.hotkey:
            return TicketCheckResponse(
                valid=False, reason="unknown_quote", hotkey=ticket.hotkey, funding=ticket.funding
            )

        payment = await retrieve_payment_by_hash(
            payment_block_hash=ticket.payment_block_hash,
            payment_extrinsic_index=str(ticket.payment_extrinsic_index),
        )
        if payment is not None and payment.agent_id is not None:
            return TicketCheckResponse(
                valid=False,
                reason="already_redeemed",
                hotkey=ticket.hotkey,
                funding=ticket.funding,
                redeemed_agent_id=payment.agent_id,
            )

        if payment is not None and payment.quote_id is not None and payment.quote_id != quote.quote_id:
            return TicketCheckResponse(
                valid=False, reason="unknown_quote", hotkey=ticket.hotkey, funding=ticket.funding
            )

        if await is_payment_refunded(
            upload_block_hash=ticket.payment_block_hash,
            upload_extrinsic_index=str(ticket.payment_extrinsic_index),
        ):
            return TicketCheckResponse(valid=False, reason="refunded", hotkey=ticket.hotkey, funding=ticket.funding)

        return TicketCheckResponse(
            valid=True,
            hotkey=ticket.hotkey,
            funding=ticket.funding,
            amount_alpha_rao=quote.amount_alpha_rao,
            expires_at=None,
        )

    credit = await get_upload_credit_by_id(credit_id=UUID(ticket.credit_id), miner_hotkey=ticket.hotkey)
    if credit is None:
        return TicketCheckResponse(valid=False, reason="unknown_credit", hotkey=ticket.hotkey, funding=ticket.funding)

    if credit.revoked_at is not None:
        return TicketCheckResponse(valid=False, reason="credit_revoked", hotkey=ticket.hotkey, funding=ticket.funding)

    if credit.redeemed_at is not None:
        return TicketCheckResponse(
            valid=False,
            reason="already_redeemed",
            hotkey=ticket.hotkey,
            funding=ticket.funding,
            redeemed_agent_id=credit.redeemed_agent_id,
        )

    if credit.expires_at is not None and as_utc(credit.expires_at) <= datetime.now(timezone.utc):
        return TicketCheckResponse(valid=False, reason="credit_expired", hotkey=ticket.hotkey, funding=ticket.funding)

    return TicketCheckResponse(
        valid=True,
        hotkey=ticket.hotkey,
        funding=ticket.funding,
        amount_alpha_rao=0,
        expires_at=credit.expires_at,
    )


@router.post("/validate-openrouter-keys", tags=["upload"], response_model=OpenRouterKeysCheckResponse)
async def validate_openrouter_keys_endpoint(body: OpenRouterKeysCheckRequest) -> OpenRouterKeysCheckResponse:
    """Pre-validate OpenRouter keys for the web upload form. Invalid keys are data (200), outages are 503."""
    try:
        await validate_openrouter_keys(
            openrouter_api_key=body.openrouter_api_key,
            openrouter_management_key=body.openrouter_management_key,
        )
    except HTTPException as exception:
        if exception.status_code == 400:
            return OpenRouterKeysCheckResponse(valid=False, reason=exception.detail)
        raise
    return OpenRouterKeysCheckResponse(valid=True, reason=None)


@router.get("/eval-pricing", tags=["eval-pricing"], response_model=UploadPriceResponse)
@hourly_cache()
async def get_upload_price() -> UploadPriceResponse:
    ALPHA_PRICE = await get_alpha_price(config.NETUID)
    eval_cost_usd = 5

    # Alpha required to cover the eval cost at the current alpha price.
    eval_cost_alpha = eval_cost_usd / ALPHA_PRICE

    # 1.1x buffer. Burned alpha is destroyed (not reclaimable), so the buffer absorbs
    # alpha-price movement between quote and burn while keeping prod uploads a bit more
    # expensive than local testing to discourage variance farming.
    amount_alpha_rao = int(eval_cost_alpha * 1e9 * 1.1)

    return UploadPriceResponse(amount_alpha_rao=amount_alpha_rao, payment_netuid=config.NETUID)
