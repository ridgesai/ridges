import secrets
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import api.config as config

admin_bearer = HTTPBearer(auto_error=False)
COMPETITION_ADMIN_ACTOR = "coldkey-ban-admin-api-key"


def require_coldkey_ban_amin(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(admin_bearer)],
) -> str:
    expected = config.COLDKEY_BAN_ADMIN_API_KEY
    if not expected:
        raise HTTPException(status_code=503, detail="Admin API is not configured")
    if credentials is None or not secrets.compare_digest(credentials.credentials, expected):
        raise HTTPException(
            status_code=401,
            detail="Invalid admin credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return COMPETITION_ADMIN_ACTOR
