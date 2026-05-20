import logging
from datetime import datetime, timedelta, timezone

import httpx
from bittensor_wallet.keypair import Keypair
from fastapi import HTTPException, UploadFile

from api.config import MINER_AGENT_UPLOAD_RATE_LIMIT_SECONDS
from queries.banned_hotkey import get_banned_hotkey
from utils.bittensor import subtensor_client

logger = logging.getLogger(__name__)


def get_miner_hotkey(file_info: str) -> str:
    miner_hotkey = file_info.split(":")[0]

    if not miner_hotkey:
        logger.warning("Upload attempt without hotkey", extra={"file_info": file_info})
        raise HTTPException(status_code=400, detail="miner_hotkey is required")

    return miner_hotkey


def check_if_python_file(filename: str) -> None:
    if not filename.endswith(".py"):
        logger.warning("Invalid file extension", extra={"filename": filename})
        raise HTTPException(status_code=400, detail="File must be a python file")


async def check_agent_banned(miner_hotkey: str) -> None:
    if await get_banned_hotkey(miner_hotkey) is not None:
        logger.warning("Blocked upload from banned hotkey", extra={"miner_hotkey": miner_hotkey})
        raise HTTPException(
            status_code=403,
            detail="Your miner hotkey has been banned for attempting to obfuscate code or otherwise cheat. If this is in error, please contact us on Discord",
        )


def check_rate_limit(latest_agent_created_at_in_latest_set_id: datetime) -> None:
    earliest_allowed_time = latest_agent_created_at_in_latest_set_id + timedelta(
        seconds=MINER_AGENT_UPLOAD_RATE_LIMIT_SECONDS
    )

    if datetime.now(timezone.utc) < earliest_allowed_time:
        logger.warning("Rate limit exceeded", extra={"latest_upload_at": str(latest_agent_created_at_in_latest_set_id)})
        raise HTTPException(
            status_code=429,
            detail=f"You must wait {MINER_AGENT_UPLOAD_RATE_LIMIT_SECONDS} seconds before uploading a new agent version",
        )


def check_signature(public_key: str, file_info: str, signature: str) -> None:
    keypair = Keypair(public_key=public_key)
    if not keypair.verify(file_info, bytes.fromhex(signature)):
        logger.warning("Invalid signature", extra={"public_key": public_key})
        raise HTTPException(status_code=400, detail="Invalid signature")


async def check_hotkey_registered(miner_hotkey: str) -> None:
    if not await subtensor_client.is_hotkey_registered(miner_hotkey):
        logger.warning("Hotkey not registered on subnet", extra={"miner_hotkey": miner_hotkey})
        raise HTTPException(status_code=400, detail="Hotkey not registered on subnet")


async def check_file_size(agent_file: UploadFile) -> str:
    MAX_FILE_SIZE = 2 * 1024 * 1024
    CHUNK_SIZE = 1024 * 1024
    file_size = 0
    chunks: list[bytes] = []
    while chunk := await agent_file.read(CHUNK_SIZE):
        file_size += len(chunk)
        if file_size > MAX_FILE_SIZE:
            logger.warning("File size exceeds limit", extra={"file_size_bytes": file_size})
            raise HTTPException(status_code=400, detail="File size must not exceed 2MB")
        chunks.append(chunk)

    await agent_file.seek(0)

    return b"".join(chunks).decode("utf-8")


async def get_tao_price() -> float:
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {"ids": "bittensor", "vs_currencies": "usd"}

    async with httpx.AsyncClient() as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        data = r.json()

    return data["bittensor"]["usd"]
