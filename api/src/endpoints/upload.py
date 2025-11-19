from bittensor import Subtensor

import asyncio
import os
import pprint
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Request
from numpy import meshgrid
from pydantic import BaseModel, Field

from api.src.utils.request_cache import hourly_cache
from queries.payments import retrieve_payment_by_hash
from utils.debug_lock import DebugLock
import utils.logger as logger
from queries.agent import create_agent, record_upload_attempt
from queries.agent import get_latest_agent_for_hotkey
from queries.banned_hotkey import get_banned_hotkey
from api.src.utils.upload_agent_helpers import get_miner_hotkey, check_if_python_file, check_agent_banned, \
    check_rate_limit, check_signature, check_hotkey_registered, check_file_size, get_tao_price
from models.agent import AgentStatus, Agent
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

    # raise HTTPException(
    #     status_code=503,
    #     detail="Uploads have been temporarily disabled while the new problem set is being implemented."
    # )

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

        # Verify payment

        # Check if payment has already been used for an agent
        existing_payment = await retrieve_payment_by_hash(
            payment_block_hash=payment_block_hash,
            payment_extrinsic_index=payment_extrinsic_index
        )

        if existing_payment is not None:
            return HTTPException(
                status_code=402,
                detail="Payment already used"
            )

        # Retrieve payment details from the chain
        print("PAYMENT BLOCK HASH -____________-")
        pprint.pprint(payment_block_hash)
        print("PAYMENT BLOCK HASH -____________-")
        print("PAYMENT EXTRINSIC INDEX -____________-")
        pprint.pprint(payment_extrinsic_index)
        print("PAYMENT EXTRINSIC INDEX -____________-")

        payment_block = subtensor.substrate.get_block(block_hash=payment_block_hash)
        print("PAYMENT BLOCK -____________-")
        pprint.pprint(payment_block)
        print("PAYMENT BLOCK -____________-")

        if payment_block is None:
            return HTTPException(
                status_code=402,
                detail="Payment could not be verified"
            )
        block_number = payment_block['header']['number']
        print("BLOCK NUMBER -____________-")
        pprint.pprint(block_number)
        print("BLOCK NUMBER -____________-")
        coldkey = subtensor.get_hotkey_owner(hotkey_ss58=miner_hotkey, block=int(block_number))
        payment_extrinsic = payment_block['extrinsics'][int(payment_extrinsic_index)]

        print(f"PAYMENT EXTRINSIC -____________-")
        pprint.pprint(payment_extrinsic)
        print(f"PAYMENT EXTRINSIC -____________-")

        # Verify amount, where it was sent
        payment_cost = await get_upload_price()

        # if payment_extrinsic['amount'] != payment_cost.amount_rao:
        #     return ""
        
        # Make sure coldkey is the same as hotkeys owner coldkey



        check_if_python_file(agent_file.filename)

        if prod:
            check_signature(public_key, file_info, signature)
            await check_hotkey_registered(miner_hotkey)
            await check_agent_banned(miner_hotkey=miner_hotkey) 
            file_content = await check_file_size(agent_file)

        agent_text = (await agent_file.read()).decode("utf-8")

        hotkey_lock = await get_hotkey_lock(miner_hotkey)
        async with DebugLock(hotkey_lock, f"Agent upload lock for miner {miner_hotkey}"):
            latest_agent: Optional[Agent] = await get_latest_agent_for_hotkey(miner_hotkey=miner_hotkey)
            if prod and latest_agent:
                check_rate_limit(latest_agent)
            agent = Agent(
                agent_id=uuid.uuid4(),
                miner_hotkey=miner_hotkey,
                name=name if not latest_agent else latest_agent.name,
                version_num=latest_agent.version_num + 1 if latest_agent else 0,
                created_at=datetime.now(),
                status=AgentStatus.screening_1,
                ip_address=request.client.host if request.client else None,
            )
            await create_agent(agent, agent_text)

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
    SEND_ADDRESS = "5F4Thj3LRZdjSAnUhymAVVq2X2czSAKD4uGNCnqW8JrCHWE4"
    TAO_PRICE = await get_tao_price() 
    
    print(f"TAO_PRICE -____________-")
    print(TAO_PRICE)
    print(f"TAO_PRICE -____________-")
    eval_cost_usd = 60

    # Get the amount of tao required per eval
    eval_cost_tao = eval_cost_usd / TAO_PRICE
    print(f"EVAL_COST_TAO -____________-")
    print(eval_cost_tao)
    print(f"EVAL_COST_TAO -____________-")
    print(f"EVAL_COST_USD -____________-")
    print(eval_cost_usd)
    print(f"EVAL_COST_USD -____________-")

    # Add a buffer against price fluctuations and eval cost variance. If this is over, we burn the difference. Determined EoD by net eval charges - net amount received
    # This also makes production evals more expensive than local by a good margin to discourage testing in production and variance farming
    amount_rao = int(eval_cost_tao * 1e9 * 1.4)
    print(f"AMOUNT_RAO -____________-")
    print(amount_rao)
    print(f"AMOUNT_RAO -____________-")

    return UploadPriceResponse(
        amount_rao=amount_rao,
        send_address=SEND_ADDRESS
    )