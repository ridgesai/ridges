from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.exception_handlers import register_exception_handlers
from api.src.endpoints import upload as upload_module
from queries.upload_price import PriceHistory
from utils.ttl import clear_all_ttl_caches
from utils.upload_pricing import PricingSettings, multiplier

NOW = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_all_ttl_caches()
    yield
    clear_all_ttl_caches()


def _client(monkeypatch, result) -> tuple[TestClient, AsyncMock]:
    history = AsyncMock(return_value=result)
    monkeypatch.setattr(upload_module, "get_competition_price_history", history)
    app = FastAPI()
    app.include_router(upload_module.router, prefix="/upload")
    register_exception_handlers(app)
    return TestClient(app), history


def test_history_returns_settings_current_price_and_purchases(monkeypatch):
    purchases = [(NOW - timedelta(minutes=70), 5.0), (NOW - timedelta(minutes=5), 5.74)]
    history = PriceHistory(
        set_id=29, settings=PricingSettings(), price_usd=6.4, as_of=datetime.now(timezone.utc), purchases=purchases
    )
    client, query = _client(monkeypatch, history)

    response = client.get("/upload/eval-pricing/history", params={"set_id": 29, "since": "2026-10-08T11:00:00"})

    assert response.status_code == 200, response.text
    assert query.call_args.args == (29, datetime(2026, 10, 8, 11, tzinfo=timezone.utc)), "a naive since is UTC"
    body = response.json()
    assert body["price_usd"] == pytest.approx(6.4, rel=1e-3)
    assert body["floor_usd"] == 5.0 and body["half_life_minutes"] == 30.0
    assert body["multiplier"] == multiplier(PricingSettings())
    assert [purchase["price_usd"] for purchase in body["purchases"]] == [5.0, 5.74]


def test_history_defaults_to_the_last_hour(monkeypatch):
    history = PriceHistory(set_id=29, settings=PricingSettings(), price_usd=5.0, as_of=NOW, purchases=[])
    client, query = _client(monkeypatch, history)

    response = client.get("/upload/eval-pricing/history", params={"set_id": 29})

    assert response.status_code == 200, response.text
    since = query.call_args.args[1]
    assert abs((datetime.now(timezone.utc) - timedelta(hours=1) - since).total_seconds()) <= 61, "rounded to the minute"


def test_history_for_missing_competition_is_404(monkeypatch):
    client, _ = _client(monkeypatch, None)
    assert client.get("/upload/eval-pricing/history", params={"set_id": 999}).status_code == 404


def test_history_rounds_since_down_to_the_minute_so_clients_share_a_window(monkeypatch):
    history = PriceHistory(
        set_id=29, settings=PricingSettings(), price_usd=5.0, as_of=datetime.now(timezone.utc), purchases=[]
    )
    client, query = _client(monkeypatch, history)

    for second in ("11:00:07", "11:00:41"):
        assert (
            client.get(
                "/upload/eval-pricing/history", params={"set_id": 29, "since": f"2026-10-08T{second}Z"}
            ).status_code
            == 200
        )

    assert query.call_count == 1, "both requests fall in the same minute, so the second one is a cache hit"
    assert query.call_args.args == (29, datetime(2026, 10, 8, 11, tzinfo=timezone.utc))


def test_history_price_is_decayed_to_the_request_time(monkeypatch):
    fetched = datetime.now(timezone.utc) - timedelta(minutes=10)
    history = PriceHistory(set_id=29, settings=PricingSettings(), price_usd=20.0, as_of=fetched, purchases=[])
    client, _ = _client(monkeypatch, history)

    body = client.get("/upload/eval-pricing/history", params={"set_id": 29}).json()

    assert body["price_usd"] == pytest.approx(20.0 * 0.5 ** (10 / 30), rel=1e-3), "a cached reading keeps decaying"
    as_of = datetime.fromisoformat(body["as_of"].replace("Z", "+00:00"))
    assert abs((datetime.now(timezone.utc) - as_of).total_seconds()) < 5
