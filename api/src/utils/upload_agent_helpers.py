import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from bittensor_wallet.keypair import Keypair
from fastapi import HTTPException, UploadFile

from api.config import MINER_AGENT_UPLOAD_RATE_LIMIT_SECONDS
from queries.banned_hotkey import get_banned_hotkey
from utils.bittensor import subtensor_client

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class BurnEvent:
    coldkey: str
    hotkey: str
    alpha_decrease: int
    netuid: int


BURN_CALL_FUNCTIONS = frozenset({"burn_alpha", "add_stake_burn"})


def get_miner_hotkey(file_info: str) -> str:
    logger.debug(f"Getting miner hotkey from file info: {file_info}.")
    miner_hotkey = file_info.split(":")[0]

    if not miner_hotkey:
        logger.error(f"A miner attempted to upload an agent without a hotkey. File info: {file_info}.")
        raise HTTPException(status_code=400, detail="miner_hotkey is required")

    logger.debug(f"Miner hotkey successfully extracted: {miner_hotkey}.")
    return miner_hotkey


def check_if_python_file(filename: str) -> None:
    logger.debug("Checking if the file is a python file...")

    if not filename.endswith(".py"):
        logger.error(f"A miner attempted to upload an agent with an invalid filename: {filename}.")
        raise HTTPException(status_code=400, detail="File must be a python file")

    logger.debug("The file is a python file.")


async def check_agent_banned(miner_hotkey: str) -> None:
    logger.debug(f"Checking if miner hotkey {miner_hotkey} is banned...")

    if await get_banned_hotkey(miner_hotkey) is not None:
        logger.error(f"A miner attempted to upload an agent with a banned hotkey: {miner_hotkey}.")
        raise HTTPException(
            status_code=403,
            detail="Your miner hotkey has been banned for attempting to obfuscate code or otherwise cheat. If this is in error, please contact us on Discord",
        )

    logger.debug(f"Miner hotkey {miner_hotkey} is not banned.")


def check_rate_limit(
    latest_agent_created_at_in_latest_set_id: datetime,
) -> None:
    logger.debug("Checking if miner is rate limited...")

    earliest_allowed_time = latest_agent_created_at_in_latest_set_id + timedelta(
        seconds=MINER_AGENT_UPLOAD_RATE_LIMIT_SECONDS
    )
    logger.debug(
        f"Earliest allowed time: {earliest_allowed_time}. Current time: {datetime.now(timezone.utc)}. Difference: {datetime.now(timezone.utc) - earliest_allowed_time}. Minimum allowed time: {timedelta(seconds=MINER_AGENT_UPLOAD_RATE_LIMIT_SECONDS)}."
    )

    if datetime.now(timezone.utc) < earliest_allowed_time:
        logger.error(
            f"A miner attempted to upload an agent too quickly. Latest agent created at {latest_agent_created_at_in_latest_set_id} and current time is {datetime.now(timezone.utc)}."
        )
        raise HTTPException(
            status_code=429,
            detail=f"You must wait {MINER_AGENT_UPLOAD_RATE_LIMIT_SECONDS} seconds before uploading a new agent version",
        )

    logger.debug("Miner is not rate limited.")


def timestamp_ms_to_utc_datetime(timestamp_ms: int | None) -> datetime:
    if timestamp_ms is None:
        raise HTTPException(status_code=402, detail="Payment block timestamp not found")
    try:
        return datetime.fromtimestamp(int(timestamp_ms) / 1000, timezone.utc)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=402,
            detail="Payment block timestamp could not be decoded",
        ) from None


def as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def check_signature(public_key: str, file_info: str, signature: str, miner_hotkey: str) -> None:
    logger.debug("Checking if the signature is valid...")
    logger.debug(f"Public key: {public_key}, File info: {file_info}, Signature: {signature}.")

    keypair = Keypair(public_key=public_key)
    if keypair.ss58_address != miner_hotkey:
        logger.error(
            f"Attempt to upload an agent with a public key that does not correspond to the miner hotkey. Public key ss58 address: {keypair.ss58_address}, Miner hotkey: {miner_hotkey}."
        )
        raise HTTPException(
            status_code=400,
            detail="Public key does not correspond to miner hotkey",
        )

    if not keypair.verify(file_info, bytes.fromhex(signature)):
        logger.error(
            f"A miner attempted to upload an agent with an invalid signature. Public key: {public_key}, File info: {file_info}, Signature: {signature}."
        )
        raise HTTPException(status_code=400, detail="Invalid signature")

    logger.debug("The signature is valid.")


async def check_hotkey_registered(miner_hotkey: str) -> None:
    logger.debug(f"Checking if miner hotkey {miner_hotkey} is registered on subnet...")

    if not await subtensor_client.is_hotkey_registered(miner_hotkey):
        logger.error(
            f"A miner attempted to upload an agent with a hotkey that is not registered on subnet: {miner_hotkey}."
        )
        raise HTTPException(status_code=400, detail="Hotkey not registered on subnet")

    logger.debug(f"Miner hotkey {miner_hotkey} is registered on the subnet.")


async def check_file_size(agent_file: UploadFile) -> tuple[bytes, str]:
    logger.debug("Checking if the file size is valid...")

    MAX_FILE_SIZE = 2 * 1024 * 1024
    CHUNK_SIZE = 1024 * 1024
    file_size = 0
    chunks: list[bytes] = []
    while chunk := await agent_file.read(CHUNK_SIZE):
        file_size += len(chunk)
        if file_size > MAX_FILE_SIZE:
            logger.error(
                f"A miner attempted to upload an agent with a file size that exceeds the maximum allowed size. File size: {file_size}."
            )
            raise HTTPException(status_code=400, detail="File size must not exceed 2MB")
        chunks.append(chunk)

    logger.debug("The file size is valid.")
    await agent_file.seek(0)

    raw = b"".join(chunks)
    return raw, raw.decode("utf-8")


async def get_tao_price() -> float:
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {"ids": "bittensor", "vs_currencies": "usd"}

    async with httpx.AsyncClient() as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        data = r.json()

    return data["bittensor"]["usd"]


async def get_alpha_price() -> float:
    """Return the SN62 alpha price in USD: (alpha price in TAO from chain) * (TAO price in USD)."""
    alpha_tao = await subtensor_client.get_alpha_price_tao()
    tao_usd = await get_tao_price()
    return alpha_tao * tao_usd


async def check_if_extrinsic_failed(extrinsic_index: int, events: list) -> bool:
    """Validate if the extrinsic failed based on the events.

    Parameters
    ----------
    extrinsic_index : int
        Index of the extrinsic in the block.
    events : list
        List of events to check.

    Returns
    -------
    bool
        True if the extrinsic failed, False otherwise.
    """
    logger.debug(f"Checking if extrinsic at index {extrinsic_index} failed based on events...")
    for event in events:
        if event.get("extrinsic_idx") != extrinsic_index:
            continue

        module = event["event"]["module_id"]
        event_id = event["event"]["event_id"]

        if module == "System" and event_id == "ExtrinsicFailed":
            return True

    return False


def _parse_alpha_burned_attributes(attributes: list | tuple | dict) -> BurnEvent:
    """Parse SubtensorModule.AlphaBurned event attributes into a BurnEvent.

    AsyncSubtensor/substrate-interface decodes this event as a positional tuple/list (coldkey, hotkey, actual_alpha_decrease, netuid) rather than named attributes.

    Named attributes are also supported as a fallback.

    Parameters
    ----------
    attributes : list|tuple|dict
        Event attributes, either as a tuple/list or a dict.

    Returns
    -------
    BurnEvent
        Parsed burn event with coldkey, hotkey, alpha_decrease, and netuid.

    """
    if isinstance(attributes, (tuple, list)) and len(attributes) >= 4:
        coldkey, hotkey, alpha_decrease, netuid = attributes[:4]
        return BurnEvent(coldkey=coldkey, hotkey=hotkey, alpha_decrease=int(alpha_decrease), netuid=int(netuid))
    if isinstance(attributes, dict):
        try:
            return BurnEvent(
                coldkey=attributes["Coldkey"],
                hotkey=attributes["Hotkey"],
                alpha_decrease=int(attributes["Actual Alpha Decrease"]),
                netuid=int(attributes["Netuid"]),
            )
        except (KeyError, TypeError, ValueError):
            raise HTTPException(status_code=402, detail="Burn event attributes could not be decoded") from None
    raise HTTPException(status_code=402, detail="Burn event attributes could not be decoded")


def find_alpha_burned_event(events: list, extrinsic_index: int, netuid: int) -> BurnEvent:
    """Find the AlphaBurned event in the events list and returned a parsed
    BurnEvent.

    Parameters
    ----------
    events : list
        List of events to search for the AlphaBurned event.
    extrinsic_index : int
        Index of the extrinsic to search for.
    netuid : int
        Netuid the burn must be on.

    Returns
    -------
    BurnEvent
        Parsed burn event with coldkey, hotkey, alpha_decrease, and netuid.
    """
    for event in events:
        if event.get("extrinsic_idx") != extrinsic_index:
            continue
        inner = event.get("event", {})
        if inner.get("module_id") == "SubtensorModule" and inner.get("event_id") == "AlphaBurned":
            alpha_burned_event = _parse_alpha_burned_attributes(inner.get("attributes", {}))
            if alpha_burned_event.netuid != netuid:
                continue
            logger.debug(f"Found AlphaBurned event: {alpha_burned_event}")
            return alpha_burned_event
    raise HTTPException(status_code=402, detail="Burn event not found")


def verify_burn_extrinsic(extrinsic: Any, expected_coldkey: str) -> None:
    """Validate that the extrinsic is a recognized alpha burn and that it was signed by the expected miner coldkey.

    Parameters
    ----------
    extrinsic : Any
        Extrinsic data to validate.
    expected_coldkey : str
        The expected coldkey of the miner.
    """
    try:
        value = extrinsic.value_serialized
        call = value["call"]
        signer = value["address"]
    except (KeyError, TypeError, AttributeError):
        raise HTTPException(status_code=402, detail="Burn extrinsic could not be decoded") from None
    logger.debug("Verifying call module and function for burn extrinsic...")
    if call.get("call_module") != "SubtensorModule" or call.get("call_function") not in BURN_CALL_FUNCTIONS:
        raise HTTPException(status_code=402, detail="Extrinsic is not a recognized alpha burn")

    logger.debug("Verifying that the burn extrinsic was signed by the expected miner coldkey...")
    if signer != expected_coldkey:
        raise HTTPException(status_code=402, detail="Burn was not signed by the miner coldkey")
    logger.debug("Burn extrinsic is valid and signed by the expected miner coldkey.")
