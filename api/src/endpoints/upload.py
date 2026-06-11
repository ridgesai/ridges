import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from api import config
from api.errors import PaymentAlreadyUsedError, PaymentRefunded, PlatformFrozenError
from api.src.utils.openrouter_validation import validate_openrouter_keys
from api.src.utils.request_cache import hourly_cache
from api.src.utils.upload_agent_helpers import (
    check_agent_banned,
    check_file_size,
    check_hotkey_registered,
    check_if_python_file,
    check_rate_limit,
    check_signature,
    get_miner_hotkey,
    get_tao_price,
)
from models.agent import Agent, AgentCreate, AgentStatus
from queries.agent import (
    create_agent,
    get_latest_agent_created_at_for_miner_hotkey_in_latest_set_id,
    get_latest_agent_for_miner_hotkey,
    record_upload_attempt,
)
from queries.banned_hotkey import get_banned_hotkey
from queries.errors import DuplicateAgentIDError
from queries.payments import (
    complete_payment,
    create_payment_quote,
    reserve_payment,
    retrieve_payment_by_hash,
    retrieve_payment_quote,
)
from queries.refund import is_payment_refunded
from utils.agent_secrets import encrypt_agent_secret
from utils.bittensor import subtensor_client
from utils.debug_lock import DebugLock

logger = logging.getLogger(__name__)

UPLOAD_PAYMENT_QUOTE_TTL_SECONDS = 60 * 60
OUTDATED_UPLOAD_CLIENT_MESSAGE = "This upload client is outdated. Please upgrade Ridges CLI and retry."

# We use a lock per hotkey to prevent multiple agents being uploaded at the same time for the same hotkey
hotkey_locks: dict[str, asyncio.Lock] = {}
hotkey_locks_lock = asyncio.Lock()


async def get_hotkey_lock(hotkey: str) -> asyncio.Lock:
    async with hotkey_locks_lock:
        if hotkey not in hotkey_locks:
            hotkey_locks[hotkey] = asyncio.Lock()
        return hotkey_locks[hotkey]


class AgentUploadResponse(BaseModel):
    """Response model for successful agent upload"""

    status: str = Field(..., description="Status of the upload operation")
    message: str = Field(..., description="Detailed message about the upload result")


class UploadPriceResponse(BaseModel):
    """Response model for upload pricing"""

    amount_rao: int = Field(..., description="Amount to send for evaluation (in RAO)")
    send_address: str = Field(..., description="TAO address to send evaluation payment to")


class AgentCheckResponse(AgentUploadResponse):
    """Response model for successful agent upload preflight checks"""

    quote_id: UUID = Field(..., description="Quote ID to include when uploading or resuming")
    amount_rao: int = Field(..., description="Amount to send for evaluation (in RAO)")
    send_address: str = Field(..., description="TAO address to send evaluation payment to")
    expires_at: datetime = Field(..., description="Latest on-chain payment timestamp accepted for this quote")


class ErrorResponse(BaseModel):
    """Error response model"""

    detail: str = Field(..., description="Error message describing what went wrong")


def _timestamp_ms_to_utc_datetime(timestamp_ms: int | None) -> datetime:
    if timestamp_ms is None:
        raise HTTPException(status_code=402, detail="Payment block timestamp not found")
    try:
        return datetime.fromtimestamp(int(timestamp_ms) / 1000, timezone.utc)
    except (TypeError, ValueError):
        raise HTTPException(status_code=402, detail="Payment block timestamp could not be decoded") from None


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


router = APIRouter()


@router.post("/agent/check", tags=["upload"], response_model=AgentCheckResponse)
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
) -> AgentCheckResponse:
    if config.DISALLOW_UPLOADS:
        raise HTTPException(status_code=503, detail=config.DISALLOW_UPLOADS_REASON)
    miner_hotkey = get_miner_hotkey(file_info)
    latest_agent_created_at_in_latest_set_id = await get_latest_agent_created_at_for_miner_hotkey_in_latest_set_id(
        miner_hotkey=miner_hotkey
    )
    if latest_agent_created_at_in_latest_set_id:
        check_rate_limit(latest_agent_created_at_in_latest_set_id)
    check_signature(public_key, file_info, signature, miner_hotkey)
    await check_hotkey_registered(miner_hotkey)
    await check_agent_banned(miner_hotkey=miner_hotkey)
    check_if_python_file(agent_file.filename)
    await check_file_size(agent_file)
    coldkey = await subtensor_client.get_hotkey_owner(miner_hotkey)
    miner_balance = (await subtensor_client.get_balance(coldkey)).rao
    payment_cost = await get_upload_price()
    if payment_cost.amount_rao > miner_balance:
        raise HTTPException(
            status_code=402,
            detail=f"Insufficient balance. You need {payment_cost.amount_rao} RAO to upload this agent. You have {miner_balance} RAO.",
        )
    await validate_openrouter_keys(
        openrouter_api_key=openrouter_api_key,
        openrouter_management_key=openrouter_management_key,
    )
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=UPLOAD_PAYMENT_QUOTE_TTL_SECONDS)
    quote = await create_payment_quote(
        miner_hotkey=miner_hotkey,
        amount_rao=payment_cost.amount_rao,
        send_address=payment_cost.send_address,
        expires_at=expires_at,
    )
    return AgentCheckResponse(
        status="success",
        message="Agent check successful",
        quote_id=quote.quote_id,
        amount_rao=quote.amount_rao,
        send_address=quote.send_address,
        expires_at=quote.expires_at,
    )


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
    payment_block_hash: str = Form(..., description="Block hash in which payment was made"),
    payment_extrinsic_index: str = Form(..., description="Index in the block for payment extrinsic"),
    quote_id: Optional[str] = Form(None, description="Server-issued upload payment quote ID"),
    openrouter_api_key: str = Form(..., description="OpenRouter API key for inference during evaluation"),
    openrouter_management_key: str = Form(
        ..., description="OpenRouter management key used to validate workspace privacy settings"
    ),
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
    prod = config.ENV == "prod"

    miner_hotkey = get_miner_hotkey(file_info)

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
        logger.info("Owner upload: " + str(is_owner_upload))

        if prod:
            check_signature(public_key, file_info, signature, miner_hotkey)

        if config.DISALLOW_UPLOADS and not is_owner_upload:
            raise PlatformFrozenError(config.DISALLOW_UPLOADS_REASON)

        if prod:
            await check_hotkey_registered(miner_hotkey)
            await check_agent_banned(miner_hotkey=miner_hotkey)

        check_if_python_file(agent_file.filename)
        agent_bytes, agent_text = await check_file_size(agent_file)
        source_sha256 = hashlib.sha256(agent_bytes).hexdigest()

        if prod and not is_owner_upload:
            if quote_id is None:
                raise HTTPException(status_code=400, detail=OUTDATED_UPLOAD_CLIENT_MESSAGE)

            try:
                quote_uuid = UUID(quote_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid payment quote ID") from None

            quote = await retrieve_payment_quote(quote_uuid)
            if quote is None:
                raise HTTPException(status_code=400, detail="Invalid payment quote ID")

            if quote.miner_hotkey != miner_hotkey:
                raise HTTPException(status_code=402, detail="Payment quote does not match upload hotkey")

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

            # Retrieve payment details from the chain
            try:
                payment_block_info = await subtensor_client.get_block_info(block_hash=payment_block_hash)
            except Exception as e:
                logger.error(f"Error retrieving payment block: {e}")
                raise HTTPException(status_code=402, detail="Payment could not be verified")

            if payment_block_info is None:
                raise HTTPException(status_code=402, detail="Payment block not found")

            coldkey = await subtensor_client.get_hotkey_owner(miner_hotkey, block=int(payment_block_info.number))
            try:
                payment_extrinsic_index_int = int(payment_extrinsic_index)
                if payment_extrinsic_index_int < 0:
                    raise ValueError
                payment_extrinsic = payment_block_info.extrinsics[payment_extrinsic_index_int]
                payment_extrinsic_value = payment_extrinsic.value_serialized
                payment_call = payment_extrinsic_value["call"]
                call_args = {arg["name"]: arg["value"] for arg in payment_call["call_args"]}
                payment_value = call_args.get("value")
                destination = call_args.get("dest")
                payment_address = payment_extrinsic_value["address"]
            except (ValueError, TypeError, IndexError, KeyError, AttributeError):
                raise HTTPException(status_code=402, detail="Payment extrinsic could not be decoded") from None

            if (
                payment_call.get("call_module") != "Balances"
                or payment_call.get("call_function") != "transfer_keep_alive"
            ):
                raise HTTPException(status_code=402, detail="Payment extrinsic is not a TAO transfer")

            if payment_value is None or await check_if_extrinsic_failed(
                payment_block_hash, payment_extrinsic_index_int
            ):
                raise HTTPException(status_code=402, detail="Payment value not found")

            # Make sure coldkey is the same as hotkeys owner coldkey
            if coldkey != payment_address:
                raise HTTPException(status_code=402, detail="Coldkey does not match")

            # Make sure destination is our upload send address
            if destination != quote.send_address:
                raise HTTPException(
                    status_code=402,
                    detail=f"Destination does not match. The payment should be sent to {quote.send_address}",
                )

            if payment_value != quote.amount_rao:
                raise HTTPException(status_code=402, detail="Payment amount does not match")

            payment_block_time = _timestamp_ms_to_utc_datetime(payment_block_info.timestamp)
            if not (_as_utc(quote.created_at) <= payment_block_time <= _as_utc(quote.expires_at)):
                raise HTTPException(status_code=402, detail="Payment was made outside the quote validity window")

        validated_openrouter_keys = await validate_openrouter_keys(
            openrouter_api_key=openrouter_api_key,
            openrouter_management_key=openrouter_management_key,
        )

        hotkey_lock = await get_hotkey_lock(miner_hotkey)
        async with DebugLock(hotkey_lock, f"Agent upload lock for miner {miner_hotkey}"):
            latest_agent: Optional[Agent] = await get_latest_agent_for_miner_hotkey(miner_hotkey=miner_hotkey)

            latest_agent_created_at_in_latest_set_id = (
                await get_latest_agent_created_at_for_miner_hotkey_in_latest_set_id(miner_hotkey=miner_hotkey)
            )

            if prod and not is_owner_upload:
                if latest_agent_created_at_in_latest_set_id:
                    check_rate_limit(latest_agent_created_at_in_latest_set_id)

                payment_row = await reserve_payment(
                    payment_block_hash=payment_block_hash,
                    payment_extrinsic_index=payment_extrinsic_index,
                    miner_hotkey=miner_hotkey,
                    miner_coldkey=coldkey,
                    amount_rao=payment_value,
                    quote_id=quote.quote_id,
                )

                if payment_row is None:
                    raise HTTPException(status_code=409, detail="Payment or quote is already reserved")

                if payment_row.agent_id is not None:
                    raise DuplicateAgentIDError(agent_id=payment_row.agent_id)

                if payment_row.quote_id != quote.quote_id:
                    raise HTTPException(status_code=409, detail="Payment is already reserved for a different quote")

            encrypted_openrouter_api_key = encrypt_agent_secret(validated_openrouter_keys.runtime_api_key)
            encrypted_openrouter_management_key = encrypt_agent_secret(validated_openrouter_keys.management_api_key)
            initial_status = (
                AgentStatus.pre_screening if config.PRE_SCREENING_JUDGE_ENABLED else AgentStatus.screening_1
            )
            agent = AgentCreate(
                miner_hotkey=miner_hotkey,
                name=name if not latest_agent else latest_agent.name,
                version_num=latest_agent.version_num + 1 if latest_agent else 0,
                created_at=datetime.now(timezone.utc),
                status=initial_status,
                ip_address=request.client.host if request.client else None,
                payment_block_hash=payment_block_hash,
                payment_extrinsic_index=payment_extrinsic_index,
            )
            agent_id = await create_agent(
                agent,
                agent_text,
                source_sha256=source_sha256,
                runtime_openrouter_api_key_ciphertext=encrypted_openrouter_api_key,
                management_openrouter_api_key_ciphertext=encrypted_openrouter_management_key,
                openrouter_workspace_id=validated_openrouter_keys.workspace_id,
                openrouter_api_key_label=validated_openrouter_keys.api_key_label,
                openrouter_api_key_creator_user_id=validated_openrouter_keys.api_key_creator_user_id,
                openrouter_validated_at=validated_openrouter_keys.validated_at,
                create_pre_screening_job=config.PRE_SCREENING_JUDGE_ENABLED,
            )

        if prod and not is_owner_upload:
            await complete_payment(
                payment_block_hash=payment_block_hash,
                payment_extrinsic_index=payment_extrinsic_index,
                agent_id=agent_id,
            )

        logger.info(f"Successfully uploaded agent {agent_id} for miner {miner_hotkey}.")

        # Record successful upload
        await record_upload_attempt(upload_type="agent", success=True, agent_id=agent_id, **upload_data)

        return AgentUploadResponse(
            status="success", message=f"Successfully uploaded agent {agent_id} for miner {miner_hotkey}."
        )

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
        banned_hotkey = await get_banned_hotkey(miner_hotkey) if error_type == "banned" and miner_hotkey else None

        # Record failed upload attempt
        await record_upload_attempt(
            upload_type="agent",
            success=False,
            error_type=error_type,
            error_message=e.detail,
            ban_reason=banned_hotkey.banned_reason if banned_hotkey else None,
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


@router.get("/eval-pricing", tags=["eval-pricing"], response_model=UploadPriceResponse)
@hourly_cache()
async def get_upload_price() -> UploadPriceResponse:
    TAO_PRICE = await get_tao_price()
    eval_cost_usd = 5

    # Get the amount of tao required per eval
    eval_cost_tao = eval_cost_usd / TAO_PRICE

    # Add a buffer against price fluctuations and eval cost variance. If this is over, we burn the difference. Determined EoD by net eval charges - net amount received
    # This also makes production evals more expensive than local by a good margin to discourage testing in production and variance farming
    amount_rao = int(eval_cost_tao * 1e9 * 1.4)

    return UploadPriceResponse(amount_rao=amount_rao, send_address=config.UPLOAD_SEND_ADDRESS)


async def check_if_extrinsic_failed(block_hash: str, extrinsic_index: int) -> bool:
    events = await subtensor_client.get_events(block_hash=block_hash)

    for event in events:
        if event.get("extrinsic_idx") != extrinsic_index:
            continue

        module = event["event"]["module_id"]
        event_id = event["event"]["event_id"]

        if module == "System" and event_id == "ExtrinsicFailed":
            return True

    return False
