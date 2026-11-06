"""Short-lived, spend-capped OpenRouter keys handed to task verifiers per evaluation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import UUID

import httpx

import api.config as config
from api.src.utils.openrouter_validation import OPENROUTER_API_BASE_URL

VERIFIER_KEY_BUDGET_USD_PER_RUN = 0.10
# OpenRouter requires remaining limit covers max_tokens x price
VERIFIER_KEY_HEADROOM_USD = 0.30
VERIFIER_KEY_TTL = timedelta(hours=24)
VERIFIER_KEY_MINT_DEADLINE_SECONDS = 10


class OpenRouterKeyError(Exception):
    """Key management failed. Messages never include keys or response bodies."""


@dataclass(frozen=True, slots=True)
class MintedKey:
    """A minted key: the secret for the verifier and the hash that deletes it."""

    secret: str = field(repr=False)
    key_hash: str


def _auth_headers() -> dict[str, str]:
    """Build the headers that authenticate with the management key."""
    return {"Authorization": f"Bearer {config.OPENROUTER_VERIFIER_MANAGEMENT_KEY}"}


async def mint_verifier_key(*, evaluation_id: UUID, run_count: int) -> MintedKey:
    """Create a key capped at the evaluation's verifier budget that expires on its own.

    Args:
        evaluation_id: The evaluation the key is for; it names the key in OpenRouter.
        run_count: How many of the evaluation's runs need the key.

    Returns:
        The new key's secret and hash.

    Raises:
        OpenRouterKeyError: OpenRouter failed, answered late or returned an incomplete key.
    """
    expires_at = datetime.now(timezone.utc) + VERIFIER_KEY_TTL
    limit = round(VERIFIER_KEY_BUDGET_USD_PER_RUN * run_count + VERIFIER_KEY_HEADROOM_USD, 2)
    body = {
        "name": f"ridges-verifier/{evaluation_id}",
        "limit": limit,
        "expires_at": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    url = f"{OPENROUTER_API_BASE_URL}/keys"

    status_code = None
    try:
        # httpx timeouts are per phase; this bounds the whole call.
        async with asyncio.timeout(VERIFIER_KEY_MINT_DEADLINE_SECONDS):
            async with httpx.AsyncClient(timeout=VERIFIER_KEY_MINT_DEADLINE_SECONDS) as client:
                response = await client.post(url, headers=_auth_headers(), json=body)
        status_code = response.status_code
        response.raise_for_status()

        payload = response.json()
        secret = payload["key"]
        key_hash = payload["data"]["hash"]
        if not (isinstance(secret, str) and secret and isinstance(key_hash, str) and key_hash):
            raise ValueError("incomplete key response")

    except Exception as exc:
        raise OpenRouterKeyError(f"Minting verifier key failed: {type(exc).__name__} (status {status_code})") from None

    return MintedKey(secret=secret, key_hash=key_hash)


async def delete_verifier_key(key_hash: str) -> None:
    """Delete a minted key. A key that is already gone counts as deleted.

    Args:
        key_hash: The hash OpenRouter returned when the key was minted.

    Raises:
        OpenRouterKeyError: OpenRouter failed or answered late.
    """
    url = f"{OPENROUTER_API_BASE_URL}/keys/{key_hash}"

    status_code = None
    try:
        async with asyncio.timeout(VERIFIER_KEY_MINT_DEADLINE_SECONDS):
            async with httpx.AsyncClient(timeout=VERIFIER_KEY_MINT_DEADLINE_SECONDS) as client:
                response = await client.delete(url, headers=_auth_headers())
        status_code = response.status_code
        if status_code != 404:
            response.raise_for_status()

    except Exception as exc:
        raise OpenRouterKeyError(f"Deleting verifier key failed: {type(exc).__name__} (status {status_code})") from None
