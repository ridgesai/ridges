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
from queries.banned_coldkey import ban_coldkey, unban_coldkey
from queries.disqualified_agent import disqualify_agent
from utils.ttl import clear_all_ttl_caches

router = APIRouter(tags=["admin"])
admin_bearer = HTTPBearer(auto_error=False)


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
    clear_all_ttl_caches()
    return disqualified
