import secrets
from typing import Annotated

from bittensor_wallet.keypair import Keypair
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, StringConstraints

import api.config as config
from api.endpoints import evaluation_sets, retrieval, scoring, statistics
from models.banned_coldkey import BannedColdkey
from queries.banned_coldkey import ban_coldkey, unban_coldkey

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


def clear_coldkey_sensitive_caches() -> None:

    cached_functions = (
        evaluation_sets._cached_build_detail,
        evaluation_sets._cached_build_leaderboard,
        evaluation_sets._cached_build_approved_agents,
        retrieval.queue,
        retrieval.top_agents,
        retrieval.top_scores_over_time,
        retrieval.perfectly_solved_over_time,
        retrieval.network_statistics,
        scoring.screener_info,
        statistics.problem_statistics,
    )
    for cached_function in cached_functions:
        cached_function.cache_clear()


@router.put(
    "/banned-coldkeys/{miner_coldkey}",
    response_model=BannedColdkey,
    dependencies=[Depends(require_coldkey_ban_admin)],
)
async def put_banned_coldkey(miner_coldkey: str, request: ColdkeyBanRequest) -> BannedColdkey:
    validate_coldkey(miner_coldkey)
    banned_coldkey = await ban_coldkey(miner_coldkey, request.reason)
    clear_coldkey_sensitive_caches()
    return banned_coldkey


@router.delete(
    "/banned-coldkeys/{miner_coldkey}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_coldkey_ban_admin)],
)
async def delete_banned_coldkey(miner_coldkey: str) -> Response:
    validate_coldkey(miner_coldkey)
    await unban_coldkey(miner_coldkey)
    clear_coldkey_sensitive_caches()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
