"""Docker token server for the task image publisher, used as Zot's bearer `realm`."""

import logging
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

import api.config as config
from api.task_image_tokens import TOKEN_TTL_SECONDS, TokenSigningUnavailable, mint_token, repository_task_digest

logger = logging.getLogger(__name__)

router = APIRouter()
publisher_basic = HTTPBasic(auto_error=False)


def _granted_access(scopes: list[str]) -> list[dict]:
    """Grant pull/push on requested task image repositories; drop every other scope."""
    access = []
    for scope in " ".join(scopes).split():
        resource_type, _, rest = scope.partition(":")
        name, _, actions = rest.rpartition(":")
        granted = sorted({"pull", "push"} & set(actions.split(",")))
        if resource_type == "repository" and granted and repository_task_digest(name):
            access.append({"type": "repository", "name": name, "actions": granted})
    return access


# /task-images/token
@router.get("/token")
def task_image_publisher_token(
    credentials: Annotated[HTTPBasicCredentials | None, Depends(publisher_basic)],
    scope: Annotated[list[str], Query()] = [],
) -> JSONResponse:
    expected = config.TASK_IMAGE_PUBLISHER_PASSWORD
    if not expected:
        raise HTTPException(status_code=503, detail="Task image publishing is not configured.")

    if credentials is None or not secrets.compare_digest(credentials.password.encode(), expected.encode()):
        raise HTTPException(
            status_code=401,
            detail="Invalid publisher credentials.",
            headers={"WWW-Authenticate": 'Basic realm="task-images"'},
        )

    try:
        token = mint_token("publisher", _granted_access(scope))
    except TokenSigningUnavailable as exc:
        logger.error(f"Task image tokens unavailable: {exc}")
        raise HTTPException(status_code=503, detail="Task image tokens are not configured.")

    return JSONResponse(
        {"token": token, "access_token": token, "expires_in": TOKEN_TTL_SECONDS},
        headers={"Cache-Control": "no-store"},
    )
