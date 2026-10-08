import hashlib
import logging
from dataclasses import dataclass
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
from models.payments import PaymentQuote
from models.upload import (
    AgentCheckResponse,
    AgentDirectCheckResponse,
    AgentUploadResponse,
    BalanceResponse,
    CancelQuoteRequest,
    ConfirmPaymentRequest,
    ErrorResponse,
    OpenRouterKeysCheckRequest,
    OpenRouterKeysCheckResponse,
    PrepareUploadRequest,
    PurchaseQuoteRequest,
    QuoteActionResponse,
    TicketCheckRequest,
    TicketCheckResponse,
    UploadPriceHistoryResponse,
    UploadPricePurchase,
    UploadPriceResponse,
)
from queries.agent import (
    BurnUploadFunding,
    CreditUploadFunding,
    PurchasedUploadFunding,
    _derive_agent_id,
    admit_agent,
    get_latest_agent_created_at_for_miner_hotkey_in_competition,
    record_upload_attempt,
)
from queries.banned_coldkey import get_banned_coldkey
from queries.burn_balance import get_burn_balance, purchase_quote
from queries.competition import get_public_competition, resolve_upload_competition
from queries.errors import (
    BurnNotReportedError,
    ColdkeyBannedError,
    CompetitionNotAcceptingSubmissionsError,
    DuplicateAgentIDError,
    InsufficientAlphaError,
    InsufficientBalanceError,
    OpenQuoteExistsError,
    QuoteAlreadyConfirmedError,
    QuoteAlreadyPurchasedError,
    QuoteCancelledError,
    QuoteNotConfirmedError,
    ReceiptConflictError,
    UploadCooldownError,
    UploadCreditAlreadyRedeemedError,
    UploadCreditUnavailableError,
    UploadFundingConflictError,
)
from queries.payments import (
    IssuedQuote,
    cancel_payment_quote,
    confirm_quote_payment,
    issue_competition_quote,
    retrieve_payment_by_hash,
    retrieve_payment_quote,
)
from queries.refund import is_payment_refunded
from queries.upload_credit import get_exact_upload_credit_replay, get_upload_credit_by_id, get_upload_credit_for_check
from queries.upload_price import PriceHistory, get_competition_price, get_competition_price_history
from utils.agent_secrets import encrypt_agent_secret
from utils.bittensor import SubtensorUnavailableError, subtensor_client
from utils.burn_receipt import canonical_receipt
from utils.s3 import upload_text_file_to_s3
from utils.ttl import ttl_cache
from utils.upload_pricing import alpha_rao_for_usd, current_price, multiplier
from utils.upload_ticket import (
    FUNDING_BURN,
    FUNDING_CREDIT,
    cancel_signing_string,
    confirm_signing_string,
    decode_ticket,
    prepare_signing_string,
    purchase_signing_string,
    verify_ticket_signature,
)

logger = logging.getLogger(__name__)

UPLOAD_PAYMENT_QUOTE_TTL_SECONDS = 15 * 60
CONFIRM_GRACE_SECONDS = 60 * 60
PRICING_VERSION = 2
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


@hourly_cache()
async def get_alpha_price_usd() -> float:
    """SN alpha price in USD, cached per clock hour."""
    return await get_alpha_price(config.NETUID)


async def _issue_quote(*, set_id: int, miner_hotkey: str, miner_coldkey: str, burnable_rao: int) -> IssuedQuote:
    """Issue (or reuse) a quote for the gap above the coldkey's balance. InsufficientAlphaError is left to the caller."""
    alpha_price_usd = await get_alpha_price_usd()
    try:
        return await issue_competition_quote(
            set_id=set_id,
            miner_hotkey=miner_hotkey,
            miner_coldkey=miner_coldkey,
            alpha_price_usd=alpha_price_usd,
            burnable_rao=burnable_rao,
            ttl_seconds=UPLOAD_PAYMENT_QUOTE_TTL_SECONDS,
        )
    except OpenQuoteExistsError as exception:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "open_quote_exists",
                "quote_id": str(exception.quote_id),
                "expires_at": exception.expires_at.isoformat(),
            },
        ) from exception
    except CompetitionNotAcceptingSubmissionsError as exception:
        raise HTTPException(status_code=409, detail=str(exception)) from exception


@dataclass(frozen=True, slots=True)
class VerifiedBurn:
    miner_coldkey: str
    amount_alpha_rao: int


async def _verify_burn_on_chain(
    *, miner_hotkey: str, quote: PaymentQuote, payment_block_hash: str, payment_extrinsic_index: str
) -> VerifiedBurn:
    """Check the receipt is a successful alpha burn by the hotkey's owner, for at least the quoted amount,
    inside the quote window. Burn authenticity only: whether the payment may still be used (already used,
    refunded, banned coldkey) is checked at redemption."""
    try:
        payment_block_info = await subtensor_client.get_block_info(block_hash=payment_block_hash)
    except SubtensorUnavailableError:
        raise

    except Exception as e:
        logger.error(f"Error retrieving payment block: {e}")
        raise HTTPException(status_code=402, detail="Payment could not be verified")

    if payment_block_info is None:
        raise HTTPException(status_code=402, detail="Payment block not found")

    try:
        extrinsic_index = int(payment_extrinsic_index)
        if extrinsic_index < 0:
            raise ValueError

        payment_extrinsic = payment_block_info.extrinsics[extrinsic_index]
    except (ValueError, TypeError, IndexError, AttributeError):
        raise HTTPException(status_code=402, detail="Burn extrinsic could not be decoded") from None

    coldkey = await subtensor_client.get_hotkey_owner(miner_hotkey, block_hash=payment_block_hash)
    if coldkey is None:
        raise HTTPException(status_code=402, detail="Hotkey owner not found at payment block")

    events = await subtensor_client.get_events(block_hash=payment_block_hash)
    if await check_if_extrinsic_failed(extrinsic_index, events):
        raise HTTPException(status_code=402, detail="Burn extrinsic failed on-chain")

    verify_burn_extrinsic(payment_extrinsic, expected_coldkey=coldkey)

    # Event is the source of truth.
    burn_event = find_alpha_burned_event(events, extrinsic_index, netuid=config.NETUID)
    if burn_event.coldkey != coldkey:
        raise HTTPException(status_code=402, detail="Coldkey does not match")

    if burn_event.hotkey != miner_hotkey:
        raise HTTPException(status_code=402, detail="Hotkey does not match")

    if burn_event.alpha_decrease < quote.amount_alpha_rao:
        raise HTTPException(status_code=402, detail="Burn amount too low")

    payment_block_time = timestamp_ms_to_utc_datetime(payment_block_info.timestamp)
    if not (as_utc(quote.created_at) <= payment_block_time <= as_utc(quote.expires_at)):
        raise HTTPException(status_code=402, detail="Payment was made outside the quote validity window")

    return VerifiedBurn(miner_coldkey=coldkey, amount_alpha_rao=burn_event.alpha_decrease)


async def _confirm_burn(
    *, quote: PaymentQuote, miner_hotkey: str, payment_block_hash: str, payment_extrinsic_index: str
) -> str:
    """Confirm a burn against its quote, bumping the competition price once. Idempotent.

    The receipt must be canonical and the caller must already own the quote. Returns "legacy" (no-op),
    "replayed" or "confirmed".
    """
    if quote.is_legacy:
        return "legacy"

    if quote.cancelled_at is not None:
        raise HTTPException(status_code=409, detail="quote_cancelled")

    if quote.confirmed_at is not None:
        receipt = (quote.confirmed_payment_block_hash, quote.confirmed_payment_extrinsic_index)
        if receipt == (payment_block_hash, payment_extrinsic_index):
            return "replayed"
        raise HTTPException(status_code=409, detail="receipt_conflict")

    verified: VerifiedBurn | None = None
    if config.ENV == "prod":
        verified = await _verify_burn_on_chain(
            miner_hotkey=miner_hotkey,
            quote=quote,
            payment_block_hash=payment_block_hash,
            payment_extrinsic_index=payment_extrinsic_index,
        )
    try:
        return await confirm_quote_payment(
            quote_id=quote.quote_id,
            payment_block_hash=payment_block_hash,
            payment_extrinsic_index=payment_extrinsic_index,
            miner_hotkey=miner_hotkey,
            miner_coldkey=None if verified is None else verified.miner_coldkey,
            amount_alpha_rao=None if verified is None else verified.amount_alpha_rao,
            grace_seconds=CONFIRM_GRACE_SECONDS,
        )
    except QuoteCancelledError as exception:
        raise HTTPException(status_code=409, detail="quote_cancelled") from exception

    except ReceiptConflictError as exception:
        raise HTTPException(status_code=409, detail="receipt_conflict") from exception

    except BurnNotReportedError as exception:
        raise HTTPException(status_code=402, detail="burn_not_reported") from exception


async def _owned_quote(quote_id: UUID, *, hotkey: str, public_key: str, signature: str, message: str) -> PaymentQuote:
    """Verify the request is signed by hotkey and that the quote belongs to it, before revealing anything."""
    try:
        check_signature(public_key, message, signature, hotkey)
    except HTTPException:
        raise

    except Exception:
        raise HTTPException(status_code=400, detail="Malformed public key or signature") from None

    quote = await retrieve_payment_quote(quote_id)
    if quote is None:
        raise HTTPException(status_code=404, detail="unknown_quote")

    if quote.miner_hotkey != hotkey:
        raise HTTPException(status_code=403, detail="not_quote_owner")
    return quote


async def _exact_credit_replay_response(
    *,
    credit_id: UUID,
    miner_hotkey: str,
    source_sha256: str,
    set_id: int,
    upload_data: dict,
) -> AgentUploadResponse | None:
    try:
        replay = await get_exact_upload_credit_replay(
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

    if replay is None:
        return None

    success_message = (
        f"Upload credit {credit_id} was already used for agent {replay.agent_id}. No new agent was created."
    )
    await record_upload_attempt(
        upload_type="agent",
        success=True,
        agent_id=replay.agent_id,
        **upload_data,
    )
    return AgentUploadResponse(
        status="success",
        message=success_message,
        agent_id=replay.agent_id,
        miner_hotkey=miner_hotkey,
        miner_coldkey=replay.miner_coldkey,
    )


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
    pricing_version: Annotated[Optional[int], Form(description="Upload pricing protocol; must be 2")] = None,
) -> AgentDirectCheckResponse:
    if config.DISALLOW_UPLOADS:
        raise HTTPException(status_code=503, detail=config.DISALLOW_UPLOADS_REASON)
    if set_id is None:
        raise HTTPException(status_code=400, detail=OUTDATED_UPLOAD_CLIENT_MESSAGE)
    if pricing_version != PRICING_VERSION:
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

    await validate_openrouter_keys(
        openrouter_api_key=openrouter_api_key,
        openrouter_management_key=openrouter_management_key,
    )
    try:
        issued = await _issue_quote(
            set_id=resolved_set_id,
            miner_hotkey=miner_hotkey,
            miner_coldkey=coldkey,
            burnable_rao=alpha_stake.burnable_rao,
        )
    except InsufficientAlphaError as exception:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Insufficient alpha. You need {exception.amount_alpha_rao} rao "
                f"burnable from the miner hotkey position on SN{config.NETUID}. "
                f"Position: {alpha_stake.position_rao}; subnet total: {alpha_stake.total_rao}; "
                f"locked: {alpha_stake.locked_rao}; burnable: {alpha_stake.burnable_rao}."
            ),
        ) from exception
    return AgentDirectCheckResponse(
        status="success",
        message="Agent check successful",
        payment_method="burn",
        quote_id=issued.quote.quote_id,
        amount_alpha_rao=issued.quote.amount_alpha_rao,
        payment_netuid=config.NETUID,
        expires_at=issued.quote.expires_at,
        price_usd=issued.upload_price_usd,
        balance_alpha_rao=issued.balance_alpha_rao,
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

    if body.set_id is None:
        raise HTTPException(status_code=400, detail=OUTDATED_UPLOAD_CLIENT_MESSAGE)
    resolved_set_id = await _resolve_upload_set_id(body.set_id)

    try:
        alpha_stake = await subtensor_client.get_alpha_stake_availability(
            coldkey=coldkey,
            hotkey=body.hotkey,
            netuid=config.NETUID,
        )
    except Exception as e:
        logger.error(f"Error retrieving burnable alpha stake: {e}")
        raise HTTPException(status_code=503, detail="Burnable alpha stake could not be verified") from e

    try:
        issued = await _issue_quote(
            set_id=resolved_set_id,
            miner_hotkey=body.hotkey,
            miner_coldkey=coldkey,
            burnable_rao=alpha_stake.burnable_rao,
        )
    except InsufficientAlphaError as exception:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Insufficient alpha. You need {exception.amount_alpha_rao} rao "
                f"burnable from the miner hotkey position on SN{config.NETUID}."
            ),
        ) from exception
    return AgentCheckResponse(
        status="success",
        message="Burn quote issued; pay, purchase, then sign and mint your ticket",
        payment_method="burn",
        quote_id=issued.quote.quote_id,
        amount_alpha_rao=issued.quote.amount_alpha_rao,
        payment_netuid=config.NETUID,
        expires_at=issued.quote.expires_at,
        price_usd=issued.upload_price_usd,
        balance_alpha_rao=issued.balance_alpha_rao,
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

        miner_burn = not is_owner_upload and not is_credit_upload
        prod_burn = prod and miner_burn
        receipt_given = payment_block_hash is not None and payment_extrinsic_index is not None

        if miner_burn and receipt_given:
            try:
                payment_block_hash, payment_extrinsic_index = canonical_receipt(
                    payment_block_hash, payment_extrinsic_index
                )
            except ValueError as exception:
                raise HTTPException(status_code=400, detail=str(exception)) from exception

        bound_quote: Optional[PaymentQuote] = None
        if miner_burn and quote_id is not None:
            try:
                candidate = await retrieve_payment_quote(UUID(quote_id))
            except ValueError:
                candidate = None
            if candidate is not None and not candidate.is_legacy and candidate.miner_hotkey == miner_hotkey:
                bound_quote = candidate
                if receipt_given:
                    await _confirm_burn(
                        quote=bound_quote,
                        miner_hotkey=miner_hotkey,
                        payment_block_hash=payment_block_hash,
                        payment_extrinsic_index=payment_extrinsic_index,
                    )

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

        if not is_credit_upload and not prod_burn and bound_quote is None:
            resolved_set_id = await _resolve_upload_set_id(set_id)

        if bound_quote is not None:
            if bound_quote.redeemed_agent_id is not None:
                raise DuplicateAgentIDError(agent_id=bound_quote.redeemed_agent_id)

            if bound_quote.purchased_at is None:
                raise HTTPException(status_code=402, detail="not_purchased")

            if bound_quote.set_id != set_id:
                raise HTTPException(status_code=409, detail="wrong_competition")

            resolved_set_id = await _resolve_upload_set_id(set_id)
            coldkey = bound_quote.miner_coldkey
            if prod:
                await check_coldkey_banned(coldkey)

        elif prod_burn:
            if quote_id is None:
                raise HTTPException(status_code=400, detail=OUTDATED_UPLOAD_CLIENT_MESSAGE)
            if not receipt_given:
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

            resolved_set_id = await _resolve_upload_set_id(set_id)

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

            verified = await _verify_burn_on_chain(
                miner_hotkey=miner_hotkey,
                quote=quote,
                payment_block_hash=payment_block_hash,
                payment_extrinsic_index=payment_extrinsic_index,
            )
            coldkey, payment_value = verified.miner_coldkey, verified.amount_alpha_rao
            await check_coldkey_banned(coldkey)

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
        elif bound_quote is not None:
            agent_payment_block_hash = f"purchase:{bound_quote.quote_id}"
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

        funding: BurnUploadFunding | CreditUploadFunding | PurchasedUploadFunding | None
        if prod and not is_owner_upload and is_credit_upload:
            funding = CreditUploadFunding(
                credit_id=credit_uuid,
                miner_hotkey=miner_hotkey,
                miner_coldkey=coldkey,
            )
        elif bound_quote is not None:
            funding = PurchasedUploadFunding(
                quote_id=bound_quote.quote_id,
                miner_hotkey=miner_hotkey,
                miner_coldkey=bound_quote.miner_coldkey,
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
                enforce_cooldown=prod and not is_owner_upload and not is_credit_upload,
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

        return AgentUploadResponse(
            status="success",
            message=success_message,
            agent_id=agent_id,
            miner_hotkey=miner_hotkey,
            miner_coldkey=admission.miner_coldkey,
        )

    except DuplicateAgentIDError as e:
        logger.warning(f"Agent upload failed, duplicate agent ID found: {e}")
        raise PaymentAlreadyUsedError() from e

    except PlatformFrozenError as e:
        logger.warning(f"Upload attempt rejected due to platform freeze: {e}")
        raise

    except SubtensorUnavailableError as e:
        await record_upload_attempt(
            upload_type="agent",
            success=False,
            error_type="internal_error",
            error_message=str(e),
            http_status_code=503,
            **upload_data,
        )
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
        payment_extrinsic_index=None
        if decoded.payment_extrinsic_index is None
        else str(decoded.payment_extrinsic_index),
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

        if not quote.is_legacy:
            competition = await get_public_competition(quote.set_id)
            bound = {
                "hotkey": ticket.hotkey,
                "funding": ticket.funding,
                "set_id": quote.set_id,
                "competition_state": None if competition is None else competition.state.value,
            }
            if quote.redeemed_agent_id is not None:
                return TicketCheckResponse(
                    valid=False, reason="already_redeemed", redeemed_agent_id=quote.redeemed_agent_id, **bound
                )
            if quote.cancelled_at is not None:
                return TicketCheckResponse(valid=False, reason="quote_cancelled", **bound)

            if quote.purchased_at is None:
                return TicketCheckResponse(valid=False, reason="not_purchased", **bound)

            if competition is None or not competition.accepting:
                return TicketCheckResponse(valid=False, reason="competition_not_accepting", **bound)
            return TicketCheckResponse(valid=True, amount_alpha_rao=quote.amount_alpha_rao, expires_at=None, **bound)

        try:
            payment_block_hash, payment_extrinsic_index = canonical_receipt(
                ticket.payment_block_hash, ticket.payment_extrinsic_index
            )
        except ValueError:
            return TicketCheckResponse(
                valid=False, reason="malformed_ticket", hotkey=ticket.hotkey, funding=ticket.funding
            )

        payment = await retrieve_payment_by_hash(
            payment_block_hash=payment_block_hash,
            payment_extrinsic_index=payment_extrinsic_index,
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
            upload_block_hash=payment_block_hash,
            upload_extrinsic_index=payment_extrinsic_index,
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
async def get_upload_price(set_id: int) -> UploadPriceResponse:
    """Current upload price of one competition. Never cached: it changes with every purchase."""
    price = await get_competition_price(set_id)
    if price is None:
        raise HTTPException(status_code=404, detail=f"Competition {set_id} not found")

    alpha_price_usd = await get_alpha_price_usd()
    return UploadPriceResponse(
        amount_alpha_rao=alpha_rao_for_usd(price.price_usd, alpha_price_usd),
        payment_netuid=config.NETUID,
        set_id=set_id,
        price_usd=price.price_usd,
        floor_usd=price.settings.floor_usd,
        target_per_hour=price.settings.target_per_hour,
        half_life_minutes=price.settings.half_life_minutes,
        multiplier=multiplier(price.settings),
        price_updated_at=price.price_updated_at,
        as_of=price.as_of,
    )


PRICE_HISTORY_CACHE_SECONDS = 5


async def _read_price_history(set_id: int, since: datetime) -> Optional[PriceHistory]:
    return await get_competition_price_history(set_id, since)


_cached_price_history = ttl_cache(ttl_seconds=PRICE_HISTORY_CACHE_SECONDS)(_read_price_history)


@router.get("/eval-pricing/history", tags=["eval-pricing"], response_model=UploadPriceHistoryResponse)
async def get_upload_price_history(set_id: int, since: Optional[datetime] = None) -> UploadPriceHistoryResponse:
    if since is None:
        since = datetime.now(timezone.utc) - timedelta(hours=1)

    elif since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)

    history = await _cached_price_history(set_id, since.replace(second=0, microsecond=0))
    if history is None:
        raise HTTPException(status_code=404, detail=f"Competition {set_id} not found")

    now = datetime.now(timezone.utc)
    return UploadPriceHistoryResponse(
        set_id=set_id,
        price_usd=current_price(
            price_usd=history.price_usd, price_updated_at=history.as_of, settings=history.settings, now=now
        ),
        as_of=now,
        floor_usd=history.settings.floor_usd,
        half_life_minutes=history.settings.half_life_minutes,
        multiplier=multiplier(history.settings),
        purchases=[UploadPricePurchase(at=at, price_usd=price_usd) for at, price_usd in history.purchases],
    )


@router.post("/payment/confirm", tags=["upload"], response_model=QuoteActionResponse)
async def confirm_payment(body: ConfirmPaymentRequest) -> QuoteActionResponse:
    """Confirm a burn right after it lands. Credits the burn balance once per quote; retries are safe."""
    try:
        payment_block_hash, payment_extrinsic_index = canonical_receipt(
            body.payment_block_hash, body.payment_extrinsic_index
        )
    except ValueError as exception:
        raise HTTPException(status_code=400, detail=str(exception)) from exception

    quote = await _owned_quote(
        body.quote_id,
        hotkey=body.hotkey,
        public_key=body.public_key,
        signature=body.signature,
        message=confirm_signing_string(body.hotkey, str(body.quote_id), payment_block_hash, payment_extrinsic_index),
    )

    status = await _confirm_burn(
        quote=quote,
        miner_hotkey=body.hotkey,
        payment_block_hash=payment_block_hash,
        payment_extrinsic_index=payment_extrinsic_index,
    )
    return QuoteActionResponse(quote_id=quote.quote_id, status=status)


@router.post("/quote/{quote_id}/cancel", tags=["upload"], response_model=QuoteActionResponse)
async def cancel_quote(quote_id: UUID, body: CancelQuoteRequest) -> QuoteActionResponse:
    """Release a quote the miner decided not to burn for. Never call it once a burn submission has started."""
    quote = await _owned_quote(
        quote_id,
        hotkey=body.hotkey,
        public_key=body.public_key,
        signature=body.signature,
        message=cancel_signing_string(body.hotkey, str(quote_id)),
    )
    if quote.is_legacy:
        return QuoteActionResponse(quote_id=quote_id, status="legacy")

    try:
        await cancel_payment_quote(quote_id)
    except QuoteAlreadyPurchasedError as exception:
        raise HTTPException(status_code=409, detail="already_purchased") from exception
    except QuoteAlreadyConfirmedError as exception:
        raise HTTPException(status_code=409, detail="already_confirmed") from exception

    return QuoteActionResponse(quote_id=quote_id, status="cancelled")


@router.post("/quote/{quote_id}/purchase", tags=["upload"], response_model=QuoteActionResponse)
async def purchase_quote_endpoint(quote_id: UUID, body: PurchaseQuoteRequest) -> QuoteActionResponse:
    """Buy the upload from the coldkey's burn balance at the competition's price right now. Idempotent."""
    quote = await _owned_quote(
        quote_id,
        hotkey=body.hotkey,
        public_key=body.public_key,
        signature=body.signature,
        message=purchase_signing_string(body.hotkey, str(quote_id)),
    )
    if quote.is_legacy:
        return QuoteActionResponse(quote_id=quote_id, status="legacy")

    if config.ENV == "prod":
        coldkey = await subtensor_client.get_hotkey_owner(body.hotkey)
        if coldkey is None:
            raise HTTPException(status_code=402, detail="Hotkey owner not found")
    else:
        coldkey = quote.miner_coldkey

    alpha_price_usd = await get_alpha_price_usd()
    try:
        status = await purchase_quote(quote_id, miner_coldkey=coldkey, alpha_price_usd=alpha_price_usd)
    except ColdkeyBannedError as exception:
        raise HTTPException(status_code=403, detail="Your miner coldkey has been banned") from exception

    except InsufficientBalanceError as exception:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "insufficient_balance",
                "price_usd": exception.price_usd,
                "price_alpha_rao": exception.price_alpha_rao,
                "balance_alpha_rao": exception.balance_alpha_rao,
                "shortfall_alpha_rao": exception.shortfall_alpha_rao,
            },
        ) from exception
    except QuoteNotConfirmedError as exception:
        raise HTTPException(status_code=402, detail="burn_not_confirmed") from exception

    except QuoteCancelledError as exception:
        raise HTTPException(status_code=409, detail="quote_cancelled") from exception

    except CompetitionNotAcceptingSubmissionsError as exception:
        raise HTTPException(status_code=409, detail=str(exception)) from exception

    return QuoteActionResponse(quote_id=quote_id, status=status)


@router.get("/balance", tags=["upload"], response_model=BalanceResponse)
async def get_burn_balance_endpoint(coldkey: str) -> BalanceResponse:
    """A coldkey's burn balance in rao."""
    return BalanceResponse(coldkey=coldkey, balance_alpha_rao=await get_burn_balance(coldkey))
