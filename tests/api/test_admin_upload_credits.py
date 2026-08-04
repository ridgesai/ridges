from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import api.config as config
import utils.database as _db
from api.endpoints import admin as admin_endpoint
from api.endpoints.admin import UploadCreditGrantRequest
from api.endpoints.admin import router as admin_router

VALID_HOTKEY = "5HB7kpn92RS7uF9uWn8bXSvPVKFPg8kPUFDd5sbveGjX6Dbi"


@pytest.fixture
async def clean_upload_credits(postgres_db):
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute("TRUNCATE upload_credits CASCADE")


def _grant_request(
    *,
    reason: str = "screener infrastructure incident",
    granted_by: str = "operator@example.com",
    grant_reference: str | None = "incident-2026-07-31",
    expires_at: datetime | None = None,
) -> UploadCreditGrantRequest:
    return UploadCreditGrantRequest(
        miner_hotkey=VALID_HOTKEY,
        reason=reason,
        granted_by=granted_by,
        grant_reference=grant_reference,
        expires_at=expires_at,
    )


@pytest.mark.anyio
async def test_admin_grants_upload_credit(clean_upload_credits) -> None:
    expires_at = datetime.now(timezone.utc) + timedelta(days=30)

    credit = await admin_endpoint.post_upload_credit(_grant_request(expires_at=expires_at))

    assert credit.miner_hotkey == VALID_HOTKEY
    assert credit.reason == "screener infrastructure incident"
    assert credit.granted_by == "operator@example.com"
    assert credit.grant_reference == "incident-2026-07-31"
    assert credit.expires_at == expires_at
    assert credit.redeemed_at is None
    assert credit.revoked_at is None


@pytest.mark.anyio
async def test_admin_grant_reference_is_idempotent(clean_upload_credits) -> None:
    first = await admin_endpoint.post_upload_credit(_grant_request())
    second = await admin_endpoint.post_upload_credit(
        _grant_request(reason="changed retry body", granted_by="different-operator@example.com")
    )

    assert second.credit_id == first.credit_id
    assert second.reason == first.reason
    assert second.granted_by == first.granted_by
    async with _db.pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM upload_credits WHERE miner_hotkey = $1 AND grant_reference = $2",
            VALID_HOTKEY,
            "incident-2026-07-31",
        )
    assert count == 1


@pytest.mark.anyio
async def test_admin_rejects_expired_credit(clean_upload_credits) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await admin_endpoint.post_upload_credit(
            _grant_request(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
        )

    assert exc_info.value.status_code == 400
    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM upload_credits") == 0


def test_admin_upload_credit_route_requires_bearer_key(monkeypatch) -> None:
    granted_credit = admin_endpoint.UploadCredit(
        credit_id="67c64261-a579-4be8-8cb5-63ad3eeb669a",
        miner_hotkey=VALID_HOTKEY,
        reason="test",
        granted_by="pytest",
        granted_at=datetime.now(timezone.utc),
    )
    grant = AsyncMock(return_value=granted_credit)
    monkeypatch.setattr(admin_endpoint, "grant_upload_credit", grant)
    app = FastAPI()
    app.include_router(admin_router, prefix="/admin")
    client = TestClient(app)
    payload = _grant_request().model_dump(mode="json")

    assert client.post("/admin/upload-credits", json=payload).status_code == 401
    assert (
        client.post(
            "/admin/upload-credits",
            json=payload,
            headers={"Authorization": "Bearer wrong-key"},
        ).status_code
        == 401
    )
    response = client.post(
        "/admin/upload-credits",
        json=payload,
        headers={"Authorization": f"Bearer {config.COLDKEY_BAN_ADMIN_API_KEY}"},
    )

    assert response.status_code == 200
    assert response.json()["credit_id"] == str(granted_credit.credit_id)
    grant.assert_awaited_once()
