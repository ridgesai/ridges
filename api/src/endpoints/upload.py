from bittensor import Subtensor

import asyncio
import os
import pprint
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Request
from numpy import meshgrid
from pydantic import BaseModel, Field

from api.src.utils.request_cache import hourly_cache
from queries.payments import retrieve_payment_by_hash, record_evaluation_payment
from utils.debug_lock import DebugLock
import utils.logger as logger
from queries.agent import create_agent, record_upload_attempt
from queries.agent import get_latest_agent_for_miner_hotkey, get_latest_agent_created_at_for_miner_hotkey_in_latest_set_id
from queries.banned_hotkey import get_banned_hotkey
from api.src.utils.upload_agent_helpers import get_miner_hotkey, check_if_python_file, check_agent_banned, \
    check_rate_limit, check_signature, check_hotkey_registered, check_file_size
from models.agent import AgentStatus, Agent
from utils.coingecko import get_tao_price
from api import config 

# TODO STEPHEN: we should have a global singleton
subtensor = Subtensor(network=config.SUBTENSOR_NETWORK)

# We use a lock per hotkey to prevent multiple agents being uploaded at the same time for the same hotkey
hotkey_locks: dict[str, asyncio.Lock] = {}
hotkey_locks_lock = asyncio.Lock()
async def get_hotkey_lock(hotkey: str) -> asyncio.Lock:
    async with hotkey_locks_lock:
        if hotkey not in hotkey_locks:
            hotkey_locks[hotkey] = asyncio.Lock()
        return hotkey_locks[hotkey]

prod = False
if os.getenv("ENV") == "prod":
    logger.info("Agent Upload running in production mode.")
    prod = True
else:
    logger.info("Agent Upload running in development mode.")

class AgentUploadResponse(BaseModel):
    """Response model for successful agent upload"""
    status: str = Field(..., description="Status of the upload operation")
    message: str = Field(..., description="Detailed message about the upload result")

class ErrorResponse(BaseModel):
    """Error response model"""
    detail: str = Field(..., description="Error message describing what went wrong")

router = APIRouter()

@router.post(
    "/agent/check",
    tags=["upload"],
    response_model=AgentUploadResponse
)
async def check_agent_post(
    request: Request,
    agent_file: UploadFile = File(..., description="Python file containing the agent code (must be named agent.py)"),
    public_key: str = Form(..., description="Public key of the miner in hex format"),
    file_info: str = Form(..., description="File information containing miner hotkey and version number (format: hotkey:version)"),
    signature: str = Form(..., description="Signature to verify the authenticity of the upload"),
    name: str = Form(..., description="Name of the agent"),
    payment_time: float = Form(..., description="Timestamp of the payment"),
) -> AgentUploadResponse:
    if config.DISALLOW_UPLOADS:
        raise HTTPException(
            status_code=503,
            detail=config.DISALLOW_UPLOADS_REASON
        )
    miner_hotkey = get_miner_hotkey(file_info)
    latest_agent_created_at_in_latest_set_id = await get_latest_agent_created_at_for_miner_hotkey_in_latest_set_id(miner_hotkey=miner_hotkey)
    if latest_agent_created_at_in_latest_set_id:
        check_rate_limit(latest_agent_created_at_in_latest_set_id)
    check_signature(public_key, file_info, signature)
    await check_hotkey_registered(miner_hotkey)
    await check_agent_banned(miner_hotkey=miner_hotkey) 
    check_if_python_file(agent_file.filename)
    coldkey = subtensor.get_hotkey_owner(hotkey_ss58=miner_hotkey)
    miner_balance = subtensor.get_balance(address=coldkey).rao
    payment_cost = await get_upload_price(cache_time=payment_time)
    if payment_cost.amount_rao > miner_balance:
        raise HTTPException(
            status_code=402,
            detail=f"Insufficient balance. You need {payment_cost.amount_rao} RAO to upload this agent. You have {miner_balance} RAO."
        )
    return AgentUploadResponse(
        status="success",
        message=f"Agent check successful"
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
        503: {"model": ErrorResponse, "description": "Service Unavailable - No screeners available for evaluation"}
    }
)
async def post_agent(
    request: Request,
    agent_file: UploadFile = File(..., description="Python file containing the agent code (must be named agent.py)"),
    public_key: str = Form(..., description="Public key of the miner in hex format"),
    file_info: str = Form(..., description="File information containing miner hotkey and version number (format: hotkey:version)"),
    signature: str = Form(..., description="Signature to verify the authenticity of the upload"),
    name: str = Form(..., description="Name of the agent"),
    payment_block_hash: str = Form(..., description="Block hash in which payment was made"),
    payment_extrinsic_index: str = Form(..., description="Index in the block for payment extrinsic"),
    payment_time: float = Form(..., description="Timestamp of the payment"),
) -> AgentUploadResponse:
    """
    Upload a new agent version for evaluation
    
    This endpoint allows miners to upload their agent code for evaluation. The agent must:
    - Be a Python file
    - Be under 1MB in size
    - Pass static code safety checks
    - Pass similarity validation to prevent copying
    - Be properly signed with the miner's keypair
    
    Rate limiting may apply based on configuration.
    """

    if config.DISALLOW_UPLOADS:
        raise HTTPException(
            status_code=503,
            detail=config.DISALLOW_UPLOADS_REASON
        )

    # Extract upload attempt data for tracking
    miner_hotkey = get_miner_hotkey(file_info)
    agent_file.file.seek(0, 2)
    file_size_bytes = agent_file.file.tell()
    agent_file.file.seek(0)
    
    upload_data = {
        'hotkey': miner_hotkey,
        'agent_name': name,
        'filename': agent_file.filename,
        'file_size_bytes': file_size_bytes,
        'ip_address': getattr(request.client, 'host', None) if request.client else None
    }
    
    try:
        logger.debug(f"Platform received a /upload/agent API request. Beginning process handle-upload-agent.")
        logger.info(f"Uploading agent {name} for miner {miner_hotkey}.")

        if prod:
            check_signature(public_key, file_info, signature)
            await check_hotkey_registered(miner_hotkey)
            await check_agent_banned(miner_hotkey=miner_hotkey) 
        check_if_python_file(agent_file.filename)

        if prod:
            # Verify payment
            # Check if payment has already been used for an agent
            existing_payment = await retrieve_payment_by_hash(
                payment_block_hash=payment_block_hash,
                payment_extrinsic_index=payment_extrinsic_index
            )

            if existing_payment is not None:
                raise HTTPException(
                    status_code=402,
                    detail="Payment already used"
                )

            # Retrieve payment details from the chain
            try:
                payment_block = subtensor.substrate.get_block(block_hash=payment_block_hash)
            except Exception as e:
                logger.error(f"Error retrieving payment block: {e}")
                raise HTTPException(
                    status_code=402,
                    detail="Payment could not be verified"
                )

            # example payment block:
            """
            {'extrinsics': [<GenericExtrinsic(value={'extrinsic_hash': '0x6b6f2be8e0d0e7721fab46da881d894dafa221b4df73ebb2b69a8c0aa5aeb01b', 'extrinsic_length': 10, 'call': {'call_index': '0x0200', 'call_function': 'set', 'call_module': 'Timestamp', 'call_args': [{'name': 'now', 'type': 'Moment', 'value': 1763573265504}], 'call_hash': '0x5cad44676af19a09d4ae5354e08570778c06b75257a932db8183b90910d0c33e'}})>,
                    <GenericExtrinsic(value={'extrinsic_hash': '0x350253844e42eda50ed13c043c6124db65189bf00a968467c763d54861492295', 'extrinsic_length': 142, 'address': '5DhaT8U7LVwnnJNUU8VL1XEipicatoaDVVq7cHo227gogVZm', 'signature': {'Sr25519': '0x2eb063251883f68aa6fad463f32d31c7f8635ec4550e1197ce1a0913b6182a065880ea5af1b68026ad996beedb803685d6d67e56e097a4d7666c7e075da2778f'}, 'era': '00', 'nonce': 14, 'tip': 0, 'mode': {'mode': 'Disabled'}, 'call': {'call_index': '0x0503', 'call_function': 'transfer_keep_alive', 'call_module': 'Balances', 'call_args': [{'name': 'dest', 'type': 'AccountIdLookupOf', 'value': '5F4Thj3LRZdjSAnUhymAVVq2X2czSAKD4uGNCnqW8JrCHWE4'}, {'name': 'value', 'type': 'Balance', 'value': 271449345}], 'call_hash': '0x20f54967ae95d9b4304d5582d8343469894c637d2d1c557c7bb0ad1f27797797'}})>],
     'header': {'digest': {'logs': [<scale_info::17(value={'PreRuntime': ('0x61757261', '0x46f877a401000000')})>,
                                    <scale_info::17(value={'Consensus': ('0x66726f6e', '0x012f7e87441378c60d18e9b676246e74ca17064ff510b10dfed2a48191648a1a9400')})>,
                                    <scale_info::17(value={'Seal': ('0x61757261', '0x44729c195bda22d4e9dce35ed7e43fd1652e7782cb38cf27cc8489fb0460af1f4c97621e5e29c19e730051df736441d3359799c7002eb81350e169bb9fcecb80')})>]},
                'extrinsicsRoot': '0x980d155f4b5a6f08d287c54e0a32380839cdfc0a5977200e33aa5787b48ec669',
                'hash': '0xb9958e4374c182785bfa4467ceb971e23882079f48524e27c08e8f5b95d8b8d8',
                'number': 13579,
                'parentHash': '0x1065e83a02ff961d45ac34a6990477de3cba102bbba2322950815e5d59f23135',
                'stateRoot': '0x301a04303fb97143649e44ca9c1d674606c8004082d11973c816ff67f2a13998'}}
            """
            block_number = payment_block['header']['number']
            coldkey = subtensor.get_hotkey_owner(hotkey_ss58=miner_hotkey, block=int(block_number))
            payment_extrinsic = payment_block['extrinsics'][int(payment_extrinsic_index)]

            payment_cost = await get_upload_price(cache_time=payment_time)

            # Example payment extrinsic:
            """
            <GenericExtrinsic(value={'extrinsic_hash': '0x350253844e42eda50ed13c043c6124db65189bf00a968467c763d54861492295', 'extrinsic_length': 142, 'address': '5DhaT8U7LVwnnJNUU8VL1XEipicatoaDVVq7cHo227gogVZm', 'signature': {'Sr25519': '0x2eb063251883f68aa6fad463f32d31c7f8635ec4550e1197ce1a0913b6182a065880ea5af1b68026ad996beedb803685d6d67e56e097a4d7666c7e075da2778f'}, 'era': '00', 'nonce': 14, 'tip': 0, 'mode': {'mode': 'Disabled'}, 'call': {'call_index': '0x0503', 'call_function': 'transfer_keep_alive', 'call_module': 'Balances', 'call_args': [{'name': 'dest', 'type': 'AccountIdLookupOf', 'value': '5F4Thj3LRZdjSAnUhymAVVq2X2czSAKD4uGNCnqW8JrCHWE4'}, {'name': 'value', 'type': 'Balance', 'value': 271449345}], 'call_hash': '0x20f54967ae95d9b4304d5582d8343469894c637d2d1c557c7bb0ad1f27797797'}})>
            """
            payment_value = None
            for arg in payment_extrinsic.value['call']['call_args']:
                if arg['name'] == 'value':
                    payment_value = arg['value']
                    break

            if payment_value is None or check_if_extrinsic_failed(payment_block_hash, int(payment_extrinsic_index)):
                raise HTTPException(
                    status_code=402,
                    detail="Payment value not found"
                )

            if payment_value != payment_cost.amount_rao:
                raise HTTPException(
                    status_code=402,
                    detail="Payment amount does not match"
                )

            # Make sure coldkey is the same as hotkeys owner coldkey
            if coldkey != payment_extrinsic['address']:
                raise HTTPException(
                    status_code=402,
                    detail="Coldkey does not match"
                )

            # Make sure destination is our upload send address
            destination = None
            for arg in payment_extrinsic.value['call']['call_args']:
                if arg['name'] == 'dest':
                    destination = arg['value']
                    break
            if destination != config.UPLOAD_SEND_ADDRESS:
                raise HTTPException(
                    status_code=402,
                    detail=f"Destination does not match. The payment should be sent to {config.UPLOAD_SEND_ADDRESS}"
                )

        agent_text = (await agent_file.read()).decode("utf-8")

        hotkey_lock = await get_hotkey_lock(miner_hotkey)
        async with DebugLock(hotkey_lock, f"Agent upload lock for miner {miner_hotkey}"):
            latest_agent: Optional[Agent] = await get_latest_agent_for_miner_hotkey(miner_hotkey=miner_hotkey)
            
            latest_agent_created_at_in_latest_set_id = await get_latest_agent_created_at_for_miner_hotkey_in_latest_set_id(miner_hotkey=miner_hotkey)
            
            if prod and latest_agent_created_at_in_latest_set_id:
                check_rate_limit(latest_agent_created_at_in_latest_set_id)
            agent = Agent(
                agent_id=uuid.uuid4(),
                miner_hotkey=miner_hotkey,
                name=name if not latest_agent else latest_agent.name,
                version_num=latest_agent.version_num + 1 if latest_agent else 0,
                created_at=datetime.now(timezone.utc),
                status=AgentStatus.screening_1,
                ip_address=request.client.host if request.client else None,
            )
            await create_agent(agent, agent_text)

        if prod:
            await record_evaluation_payment(
                payment_block_hash=payment_block_hash,
                payment_extrinsic_index=payment_extrinsic_index,
                amount_rao=payment_value,
                agent_id=agent.agent_id,
                miner_hotkey=miner_hotkey,
                miner_coldkey=coldkey
            )

        logger.info(f"Successfully uploaded agent {agent.agent_id} for miner {miner_hotkey}.")

        # Record successful upload
        await record_upload_attempt(
            upload_type="agent",
            success=True,
            agent_id=agent.agent_id,
            **upload_data
        )

        return AgentUploadResponse(
            status="success",
            message=f"Successfully uploaded agent {agent.agent_id} for miner {miner_hotkey}."
        )
    
    except HTTPException as e:
        # Determine error type and get ban reason if applicable
        error_type = 'banned' if e.status_code == 403 and 'banned' in e.detail.lower() else \
                    'rate_limit' if e.status_code == 429 else 'validation_error'
        banned_hotkey = await get_banned_hotkey(miner_hotkey) if error_type == 'banned' and miner_hotkey else None
        
        # Record failed upload attempt
        await record_upload_attempt(
            upload_type="agent",
            success=False,
            error_type=error_type,
            error_message=e.detail,
            ban_reason=banned_hotkey.banned_reason if banned_hotkey else None,
            http_status_code=e.status_code,
            **upload_data
        )
        raise
    
    except Exception as e:
        # Record internal error
        await record_upload_attempt(
            upload_type="agent",
            success=False,
            error_type='internal_error',
            error_message=str(e),
            http_status_code=500,
            **upload_data
        )
        raise


class UploadPriceResponse(BaseModel):
    """Response model for successful agent upload"""
    amount_rao: int = Field(..., description="Amount to send for evaluation (in RAO)")
    send_address: str = Field(..., description="TAO address to send evaluation payment to")

@router.get(
    "/eval-pricing",
    tags=["eval-pricing"],
    response_model=UploadPriceResponse
)
@hourly_cache()
async def get_upload_price() -> UploadPriceResponse:
    TAO_PRICE = await get_tao_price() 
    eval_cost_usd = 60

    # Get the amount of tao required per eval
    eval_cost_tao = eval_cost_usd / TAO_PRICE

    # Add a buffer against price fluctuations and eval cost variance. If this is over, we burn the difference. Determined EoD by net eval charges - net amount received
    # This also makes production evals more expensive than local by a good margin to discourage testing in production and variance farming
    amount_rao = int(eval_cost_tao * 1e9 * 1.4)

    return UploadPriceResponse(
        amount_rao=amount_rao,
        send_address=config.UPLOAD_SEND_ADDRESS
    )

def check_if_extrinsic_failed(block_hash: str, extrinsic_index: int) -> bool:
    events = subtensor.substrate.get_events(block_hash=block_hash)

    for event in events:
        if event.get("extrinsic_idx") != extrinsic_index:
            continue

        module = event["event"]["module_id"]
        event_id = event["event"]["event_id"]

        if module == "System" and event_id == "ExtrinsicFailed":
            return True

    return False
