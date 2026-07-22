from __future__ import annotations

import pytest
from pydantic import BaseModel

import validator.http_utils as http_utils


class _Body(BaseModel):
    field: str = "value"


class _FakeResponse:
    status_code = 200
    reason_phrase = "OK"

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {}


class _CaptureClient:
    def __init__(self, captured: list):
        self._captured = captured

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, url, headers=None):
        self._captured.append(("GET", url, headers))
        return _FakeResponse()

    async def post(self, url, json=None, headers=None):
        self._captured.append(("POST", url, headers))
        return _FakeResponse()


@pytest.fixture()
def captured_requests(monkeypatch) -> list:
    captured: list = []
    monkeypatch.setattr(http_utils.httpx, "AsyncClient", lambda **_kwargs: _CaptureClient(captured))
    monkeypatch.setattr(http_utils.config, "RIDGES_PLATFORM_URL", "http://platform.test", raising=False)
    return captured


@pytest.mark.anyio
async def test_post_attaches_screener_edge_key_header_when_configured(monkeypatch, captured_requests) -> None:
    monkeypatch.setattr(http_utils.config, "SCREENER_EDGE_KEY", "edge-secret", raising=False)

    await http_utils.post_ridges_platform("/validator/heartbeat", _Body(), bearer_token="session-token", quiet=2)

    _method, _url, headers = captured_requests[0]
    assert headers["X-Screener-Edge-Key"] == "edge-secret"
    assert headers["Authorization"] == "Bearer session-token"


@pytest.mark.anyio
async def test_post_sends_no_headers_when_edge_key_unset_and_no_bearer(monkeypatch, captured_requests) -> None:
    monkeypatch.setattr(http_utils.config, "SCREENER_EDGE_KEY", None, raising=False)

    await http_utils.post_ridges_platform("/validator/heartbeat", _Body(), quiet=2)

    _method, _url, headers = captured_requests[0]
    assert headers is None


@pytest.mark.anyio
async def test_get_attaches_screener_edge_key_header_when_configured(monkeypatch, captured_requests) -> None:
    monkeypatch.setattr(http_utils.config, "SCREENER_EDGE_KEY", "edge-secret", raising=False)

    await http_utils.get_ridges_platform("/evaluation-sets/all", quiet=2)

    _method, _url, headers = captured_requests[0]
    assert headers["X-Screener-Edge-Key"] == "edge-secret"


@pytest.mark.anyio
async def test_get_sends_no_headers_when_edge_key_unset(monkeypatch, captured_requests) -> None:
    monkeypatch.setattr(http_utils.config, "SCREENER_EDGE_KEY", None, raising=False)

    await http_utils.get_ridges_platform("/evaluation-sets/all", quiet=2)

    _method, _url, headers = captured_requests[0]
    assert headers is None
