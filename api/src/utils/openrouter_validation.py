from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import HTTPException

OPENROUTER_API_BASE_URL = "https://openrouter.ai/api/v1"


@dataclass(slots=True, frozen=True)
class ValidatedOpenRouterKeys:
    runtime_api_key: str
    management_api_key: str
    workspace_id: str
    api_key_label: str
    api_key_creator_user_id: str
    validated_at: datetime


def _normalized_secret(value: str | None, *, field_name: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail=f"{field_name} is required")
    return normalized


def _workspace_has_unsafe_logging(workspace: dict[str, Any]) -> bool:
    return any(
        bool(workspace.get(field))
        for field in (
            "is_observability_io_logging_enabled",
            "is_observability_broadcast_enabled",
            "is_data_discount_logging_enabled",
        )
    )


async def _request_json(
    client: httpx.AsyncClient,
    *,
    path: str,
    bearer_token: str,
    invalid_key_detail: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        response = await client.get(
            f"{OPENROUTER_API_BASE_URL}{path}",
            headers={"Authorization": f"Bearer {bearer_token}"},
            params=params,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Failed to validate OpenRouter keys with OpenRouter: {type(exc).__name__}: {exc}",
        ) from exc

    if response.status_code == 401:
        raise HTTPException(status_code=400, detail=invalid_key_detail)
    if response.status_code >= 400:
        raise HTTPException(
            status_code=503,
            detail=(f"Failed to validate OpenRouter keys with OpenRouter: {response.status_code} {response.text}"),
        )

    return response.json()


async def validate_openrouter_keys(
    *,
    openrouter_api_key: str | None,
    openrouter_management_key: str | None,
) -> ValidatedOpenRouterKeys:
    runtime_api_key = _normalized_secret(openrouter_api_key, field_name="openrouter_api_key")
    management_api_key = _normalized_secret(openrouter_management_key, field_name="openrouter_management_key")

    async with httpx.AsyncClient(timeout=15.0) as client:
        current_key = await _request_json(
            client,
            path="/key",
            bearer_token=runtime_api_key,
            invalid_key_detail="Invalid OpenRouter API key",
        )
        key_data = current_key.get("data") or {}
        api_key_label = key_data.get("label")
        api_key_creator_user_id = key_data.get("creator_user_id")
        if not api_key_label or not api_key_creator_user_id:
            raise HTTPException(
                status_code=503,
                detail="OpenRouter key inspection response did not include label and creator_user_id",
            )

        workspaces_response = await _request_json(
            client,
            path="/workspaces",
            bearer_token=management_api_key,
            invalid_key_detail="Invalid OpenRouter management key",
        )
        workspaces = workspaces_response.get("data") or []

        matching_workspace_ids: list[str] = []
        for workspace in workspaces:
            workspace_id = workspace.get("id")
            if not workspace_id:
                continue
            keys_response = await _request_json(
                client,
                path="/keys",
                bearer_token=management_api_key,
                invalid_key_detail="Invalid OpenRouter management key",
                params={"workspace_id": workspace_id},
            )
            for key_row in keys_response.get("data") or []:
                if (
                    key_row.get("label") == api_key_label
                    # and key_row.get("creator_user_id") == api_key_creator_user_id
                ):
                    matching_workspace_ids.append(workspace_id)

        if not matching_workspace_ids:
            raise HTTPException(
                status_code=400,
                detail="The provided OpenRouter API key is not visible to the provided management key",
            )
        if len(matching_workspace_ids) > 1:
            raise HTTPException(
                status_code=400,
                detail="The provided OpenRouter API key matched multiple OpenRouter key records",
            )

        workspace_id = matching_workspace_ids[0]
        workspace_response = await _request_json(
            client,
            path=f"/workspaces/{workspace_id}",
            bearer_token=management_api_key,
            invalid_key_detail="Invalid OpenRouter management key",
        )
        workspace_data = workspace_response.get("data") or {}
        if _workspace_has_unsafe_logging(workspace_data):
            raise HTTPException(
                status_code=400,
                detail=(
                    "The OpenRouter workspace for this key must disable input/output logging, "
                    "broadcast, and data discount logging"
                ),
            )

    return ValidatedOpenRouterKeys(
        runtime_api_key=runtime_api_key,
        management_api_key=management_api_key,
        workspace_id=workspace_id,
        api_key_label=api_key_label,
        api_key_creator_user_id=api_key_creator_user_id,
        validated_at=datetime.now(timezone.utc),
    )
