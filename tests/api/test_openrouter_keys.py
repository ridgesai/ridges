from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest

import api.config as config
from api.src.utils import openrouter_keys
from api.src.utils import sentry as sentry_module
from api.src.utils.openrouter_keys import MintedKey, OpenRouterKeyError, delete_verifier_key, mint_verifier_key
from api.src.utils.openrouter_validation import OPENROUTER_API_BASE_URL

pytestmark = pytest.mark.anyio

MANAGEMENT_KEY = "mgmt-secret-value"


@pytest.fixture(autouse=True)
def management_key(monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_VERIFIER_MANAGEMENT_KEY", MANAGEMENT_KEY)


def _route(monkeypatch, handler):
    """Send the module's httpx traffic to an in-process handler."""
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        openrouter_keys.httpx,
        "AsyncClient",
        lambda *args, **kwargs: real_client(*args, transport=transport, **kwargs),
    )


async def test_mint_posts_name_limit_and_expiry(monkeypatch):
    evaluation_id = uuid4()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"data": {"hash": "h1"}, "key": "sk-or-v1-x"})

    _route(monkeypatch, handler)
    minted = await mint_verifier_key(evaluation_id=evaluation_id, run_count=25)

    assert minted == MintedKey(secret="sk-or-v1-x", key_hash="h1")
    (request,) = seen
    assert request.method == "POST"
    assert str(request.url) == f"{OPENROUTER_API_BASE_URL}/keys"
    assert request.headers["Authorization"] == f"Bearer {MANAGEMENT_KEY}"
    body = json.loads(request.content)
    assert body["name"] == f"ridges-verifier/{evaluation_id}"
    assert body["limit"] == 2.8  # 25 runs x $0.10 + $0.30 headroom
    assert body["expires_at"].endswith("Z")
    expires_at = datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00"))
    expected = datetime.now(timezone.utc) + timedelta(hours=24)
    assert abs((expires_at - expected).total_seconds()) < 60


def _status_500(_request):
    return httpx.Response(500, text=f"upstream echoed Bearer {MANAGEMENT_KEY}")


def _connect_error(request):
    raise httpx.ConnectError("connection refused", request=request)


def _not_json(_request):
    return httpx.Response(200, text="<html>sk-or-v1-leaked</html>")


def _missing_key(_request):
    return httpx.Response(200, json={"data": {"hash": "h1"}})


def _empty_hash(_request):
    return httpx.Response(200, json={"data": {"hash": ""}, "key": "sk-or-v1-x"})


@pytest.mark.parametrize("handler", [_status_500, _connect_error, _not_json, _missing_key, _empty_hash])
async def test_mint_errors_are_sanitized(monkeypatch, handler):
    _route(monkeypatch, handler)

    with pytest.raises(OpenRouterKeyError) as excinfo:
        await mint_verifier_key(evaluation_id=uuid4(), run_count=1)

    assert MANAGEMENT_KEY not in str(excinfo.value)
    assert "sk-or" not in str(excinfo.value)
    assert excinfo.value.__cause__ is None


async def test_mint_gives_up_at_the_deadline(monkeypatch):
    monkeypatch.setattr(openrouter_keys, "VERIFIER_KEY_MINT_DEADLINE_SECONDS", 0.05)

    class SlowClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def post(self, *args, **kwargs):
            await asyncio.sleep(5)

    monkeypatch.setattr(openrouter_keys.httpx, "AsyncClient", SlowClient)

    started = time.monotonic()
    with pytest.raises(OpenRouterKeyError):
        await mint_verifier_key(evaluation_id=uuid4(), run_count=1)
    # Without the deadline the slow call would run its full 5 seconds.
    assert time.monotonic() - started < 1


@pytest.mark.parametrize("status_code", [200, 204, 404])
async def test_delete_succeeds_on_ok_and_404(monkeypatch, status_code):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status_code)

    _route(monkeypatch, handler)
    await delete_verifier_key("h1")

    (request,) = seen
    assert request.method == "DELETE"
    assert str(request.url) == f"{OPENROUTER_API_BASE_URL}/keys/h1"
    assert request.headers["Authorization"] == f"Bearer {MANAGEMENT_KEY}"


async def test_delete_raises_on_server_error(monkeypatch):
    _route(monkeypatch, _status_500)

    with pytest.raises(OpenRouterKeyError) as excinfo:
        await delete_verifier_key("h1")

    assert MANAGEMENT_KEY not in str(excinfo.value)


def test_minted_key_repr_hides_secret():
    assert "sk-or" not in repr(MintedKey(secret="sk-or-v1-x", key_hash="h1"))


def test_sentry_disables_local_variables(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(sentry_module.config, "SENTRY_DSN", "https://public@example.invalid/1")
    monkeypatch.setattr(sentry_module.sentry_sdk, "init", lambda **kwargs: calls.append(kwargs))

    sentry_module.initialize_sentry()

    (kwargs,) = calls
    assert kwargs["include_local_variables"] is False
