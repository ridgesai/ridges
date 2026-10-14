from datetime import datetime, timezone
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import config
from api.endpoints import admin as admin_endpoint
from api.endpoints.admin import router as admin_router
from api.exception_handlers import register_exception_handlers
from queries.upload_price import CompetitionPrice
from utils.upload_pricing import PricingSettings

NOW = datetime(2026, 10, 6, tzinfo=timezone.utc)
PAYLOAD = {"floor_usd": 6.0, "target_per_hour": 12.0, "half_life_minutes": 20.0, "reason": "tune after comp 29"}


def _client(monkeypatch, result) -> tuple[TestClient, AsyncMock]:
    update = AsyncMock(return_value=result)
    monkeypatch.setattr(admin_endpoint, "update_competition_pricing", update)
    app = FastAPI()
    app.include_router(admin_router, prefix="/admin")
    register_exception_handlers(app)
    return TestClient(app), update


def _auth() -> dict:
    return {"Authorization": f"Bearer {config.COLDKEY_BAN_ADMIN_API_KEY}"}


def test_update_saves_settings_and_returns_snapshot(monkeypatch):
    settings = PricingSettings(floor_usd=6.0, target_per_hour=12.0, half_life_minutes=20.0)
    price = CompetitionPrice(set_id=1, settings=settings, price_usd=6.0, price_updated_at=NOW, as_of=NOW)
    client, update = _client(monkeypatch, price)
    response = client.put("/admin/competitions/1/upload-pricing", json=PAYLOAD, headers=_auth())
    assert response.status_code == 200, response.text
    assert update.call_args.args == (1, settings)
    body = response.json()
    assert body["price_usd"] == 6.0
    assert body["multiplier"] == 2 ** (60 / (12 * 20))


def test_update_rejects_multiplier_above_one_and_a_half(monkeypatch):
    client, update = _client(monkeypatch, None)
    payload = dict(PAYLOAD, target_per_hour=2.0, half_life_minutes=30.0)
    assert client.put("/admin/competitions/1/upload-pricing", json=payload, headers=_auth()).status_code == 422
    update.assert_not_called()


def test_update_unknown_competition_is_404(monkeypatch):
    client, _ = _client(monkeypatch, None)
    assert client.put("/admin/competitions/9/upload-pricing", json=PAYLOAD, headers=_auth()).status_code == 404


def test_update_requires_admin_auth(monkeypatch):
    client, update = _client(monkeypatch, None)
    response = client.put(
        "/admin/competitions/1/upload-pricing", json=PAYLOAD, headers={"Authorization": "Bearer wrong"}
    )
    assert response.status_code == 401
    update.assert_not_called()
