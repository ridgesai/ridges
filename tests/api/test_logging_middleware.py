import logging

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from utils.logger import RidgesLogHandler


@pytest.fixture(autouse=True)
def setup_logging_handler():
    """Attach RidgesLogHandler to the root logger so capsys captures print() output."""
    handler = RidgesLogHandler()
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    yield
    root.removeHandler(handler)


@pytest.fixture
def app_with_middleware():
    from api.src.middleware.logging import LoggingMiddleware

    app = FastAPI()
    app.add_middleware(LoggingMiddleware)

    @app.post("/test")
    async def endpoint():
        return JSONResponse({"ok": True})

    return app


@pytest.mark.anyio
async def test_request_is_logged(app_with_middleware, capsys):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_with_middleware), base_url="http://test"
    ) as client:
        resp = await client.post("/test", json={"name": "alice"})

    assert resp.status_code == 200
    out = capsys.readouterr().out
    assert "POST" in out
    assert "/test" in out


@pytest.mark.anyio
async def test_sensitive_fields_are_redacted(app_with_middleware, capsys):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_with_middleware), base_url="http://test"
    ) as client:
        await client.post("/test", json={"name": "alice", "signature": "abc123", "api_key": "secret"})

    out = capsys.readouterr().out
    assert "abc123" not in out
    assert "secret" not in out
    assert "[REDACTED]" in out


@pytest.mark.anyio
async def test_response_includes_status_and_duration(app_with_middleware, capsys):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_with_middleware), base_url="http://test"
    ) as client:
        await client.post("/test", json={})

    out = capsys.readouterr().out
    assert "200" in out
    assert "duration_ms" in out


@pytest.mark.anyio
async def test_query_params_are_logged(app_with_middleware, capsys):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_with_middleware), base_url="http://test"
    ) as client:
        await client.post("/test?foo=bar", json={})

    out = capsys.readouterr().out
    assert "foo" in out
    assert "bar" in out
