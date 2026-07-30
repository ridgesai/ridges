import asyncio
import logging
import secrets
from typing import Annotated
from uuid import UUID

from bittensor_wallet.keypair import Keypair
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, StringConstraints

import api.config as config
from models.banned_coldkey import BannedColdkey
from models.disqualified_agent import DisqualifiedAgent
from queries.agent import get_agent_by_id
from queries.approval import process_pending_disqualification_jobs
from queries.banned_coldkey import ban_coldkey, unban_coldkey
from queries.disqualification_job import enqueue_disqualification_job
from queries.disqualified_agent import disqualify_agent
from utils.database import DatabaseConnection, db_operation
from utils.ttl import clear_all_ttl_caches

logger = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])
admin_bearer = HTTPBearer(auto_error=False)

# Retains references to fire-and-forget drain tasks so they can't be garbage-collected
# before completion (asyncio only holds a weak reference to a bare create_task result).
_background_tasks: set[asyncio.Task[None]] = set()


class ColdkeyBanRequest(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


def require_coldkey_ban_admin(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(admin_bearer)],
) -> None:
    expected = config.COLDKEY_BAN_ADMIN_API_KEY
    if not expected:
        raise HTTPException(status_code=503, detail="Coldkey ban administration is not configured")
    if credentials is None or not secrets.compare_digest(credentials.credentials, expected):
        raise HTTPException(
            status_code=401,
            detail="Invalid admin credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


def validate_coldkey(miner_coldkey: str) -> None:
    try:
        Keypair(ss58_address=miner_coldkey)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid coldkey SS58 address") from None


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


@router.put(
    "/disqualified-agents/{agent_id}",
    response_model=DisqualifiedAgent,
    dependencies=[Depends(require_coldkey_ban_admin)],
)
async def put_disqualified_agent(agent_id: UUID, request: ColdkeyBanRequest) -> DisqualifiedAgent:
    agent = await get_agent_by_id(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    disqualified = await disqualify_agent(agent_id, request.reason)

    set_id = await _enqueue_disqualification_job_operation(agent_id=agent_id)
    if set_id is not None:
        _fire_disqualification_drain()

    clear_all_ttl_caches()
    return disqualified


@db_operation
async def _enqueue_disqualification_job_operation(conn: DatabaseConnection, *, agent_id: UUID) -> int | None:
    """Enqueue a reapproval job for the agent's set. Returns the set_id, or None if the agent has none."""
    async with conn.conn.transaction():
        set_id = await conn.fetchval("SELECT set_id FROM agents WHERE agent_id = $1", agent_id)
        if set_id is None:
            return None
        await enqueue_disqualification_job(conn, agent_id=agent_id, set_id=set_id)
        return set_id


async def _run_disqualification_drain() -> None:
    try:
        await process_pending_disqualification_jobs()
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Disqualification drain task failed: {type(exc).__name__}: {exc}")


def _fire_disqualification_drain() -> None:
    """Fire the drain as a background task, retaining a reference until it completes."""
    task = asyncio.create_task(_run_disqualification_drain())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
