import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from bittensor_wallet.keypair import Keypair
from fastapi import HTTPException

import utils.database as _db
from api.src.endpoints import upload as upload_module
from models.upload import PrepareUploadRequest
from utils.upload_ticket import prepare_signing_string

KEYPAIR = Keypair.create_from_seed("0x" + "ab" * 32)
HOTKEY = KEYPAIR.ss58_address
FAKE_COLDKEY = "5FColdKey456"
FAKE_AMOUNT_ALPHA_RAO = 120_344_620_287_164

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module", autouse=True)
def upload_prod_mode():
    original_env = upload_module.config.ENV
    upload_module.config.ENV = "prod"
    yield
    upload_module.config.ENV = original_env


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db):
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE evaluation_payments, upload_credits, upload_payment_quotes, agents, banned_coldkeys, "
            "failed_upload_refunds, upload_attempts RESTART IDENTITY CASCADE"
        )


@pytest.fixture(autouse=True)
def chain_mocks(monkeypatch):
    monkeypatch.setattr(upload_module, "check_hotkey_registered", AsyncMock())
    monkeypatch.setattr(upload_module.subtensor_client, "get_hotkey_owner", AsyncMock(return_value=FAKE_COLDKEY))
    monkeypatch.setattr(
        upload_module.subtensor_client,
        "get_alpha_stake_availability",
        AsyncMock(
            return_value=SimpleNamespace(
                position_rao=FAKE_AMOUNT_ALPHA_RAO * 10,
                total_rao=FAKE_AMOUNT_ALPHA_RAO * 10,
                locked_rao=0,
                burnable_rao=FAKE_AMOUNT_ALPHA_RAO * 10,
            )
        ),
    )
    monkeypatch.setattr(
        upload_module,
        "get_upload_price",
        AsyncMock(
            return_value=MagicMock(amount_alpha_rao=FAKE_AMOUNT_ALPHA_RAO, payment_netuid=upload_module.config.NETUID)
        ),
    )


def _request(**overrides) -> PrepareUploadRequest:
    fields = {
        "hotkey": HOTKEY,
        "public_key": KEYPAIR.public_key.hex(),
        "signature": KEYPAIR.sign(prepare_signing_string(HOTKEY)).hex(),
        "use_credit": False,
        "credit_id": None,
    }
    fields.update(overrides)
    return PrepareUploadRequest(**fields)


async def _insert_credit(hotkey: str = HOTKEY) -> uuid.UUID:
    credit_id = uuid.uuid4()
    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO upload_credits (credit_id, miner_hotkey, reason, granted_by, granted_at)
            VALUES ($1, $2, 'test credit', 'pytest', $3)
            """,
            credit_id,
            hotkey,
            datetime.now(timezone.utc),
        )
    return credit_id


async def test_burn_prepare_issues_quote_with_real_signature():
    response = await upload_module.prepare_upload(_request())

    assert response.payment_method == "burn"
    assert response.amount_alpha_rao == FAKE_AMOUNT_ALPHA_RAO
    assert response.payment_netuid == upload_module.config.NETUID
    assert response.expires_at is not None
    async with _db.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT miner_hotkey FROM upload_payment_quotes WHERE quote_id = $1", response.quote_id
        )
    assert row["miner_hotkey"] == HOTKEY


async def test_prepare_rejects_bad_signature():
    with pytest.raises(HTTPException) as exc:
        await upload_module.prepare_upload(_request(signature=KEYPAIR.sign("wrong message").hex()))
    assert exc.value.status_code == 400
    async with _db.pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM upload_payment_quotes WHERE miner_hotkey = $1", HOTKEY)
    assert count == 0


async def test_prepare_rejects_when_frozen(monkeypatch):
    monkeypatch.setattr(upload_module.config, "DISALLOW_UPLOADS", True)
    # raising=False: DISALLOW_UPLOADS_REASON only exists as a module attribute when
    # DISALLOW_UPLOADS was true at api.config import time (see api/config.py); the test
    # env sets it false, so the attribute is absent until we set it here.
    monkeypatch.setattr(upload_module.config, "DISALLOW_UPLOADS_REASON", "frozen for test", raising=False)
    with pytest.raises(HTTPException) as exc:
        await upload_module.prepare_upload(_request())
    assert exc.value.status_code == 503


async def test_burn_prepare_enforces_rate_limit(monkeypatch):
    monkeypatch.setattr(
        upload_module,
        "get_latest_agent_created_at_for_miner_hotkey_in_latest_set_id",
        AsyncMock(return_value=datetime.now(timezone.utc)),
    )
    with pytest.raises(HTTPException) as exc:
        await upload_module.prepare_upload(_request())
    assert exc.value.status_code == 429


async def test_burn_prepare_rejects_insufficient_stake(monkeypatch):
    monkeypatch.setattr(
        upload_module.subtensor_client,
        "get_alpha_stake_availability",
        AsyncMock(return_value=SimpleNamespace(position_rao=0, total_rao=0, locked_rao=0, burnable_rao=0)),
    )
    with pytest.raises(HTTPException) as exc:
        await upload_module.prepare_upload(_request())
    assert exc.value.status_code == 402


async def test_credit_prepare_returns_credit_and_skips_rate_limit(monkeypatch):
    credit_id = await _insert_credit()
    monkeypatch.setattr(
        upload_module,
        "get_latest_agent_created_at_for_miner_hotkey_in_latest_set_id",
        AsyncMock(return_value=datetime.now(timezone.utc)),
    )
    monkeypatch.setattr(
        upload_module,
        "check_rate_limit",
        MagicMock(side_effect=AssertionError("credit prepare must not check the cooldown")),
    )

    response = await upload_module.prepare_upload(_request(use_credit=True))

    assert response.payment_method == "credit"
    assert response.credit_id == credit_id
    assert response.amount_alpha_rao == 0


async def test_credit_prepare_402_when_no_credit():
    with pytest.raises(HTTPException) as exc:
        await upload_module.prepare_upload(_request(use_credit=True))
    assert exc.value.status_code == 402


async def test_credit_prepare_rejected_outside_prod():
    original = upload_module.config.ENV
    upload_module.config.ENV = "dev"
    try:
        with pytest.raises(HTTPException) as exc:
            await upload_module.prepare_upload(_request(use_credit=True))
        assert exc.value.status_code == 400
    finally:
        upload_module.config.ENV = original


async def test_prepare_rejects_credit_id_without_use_credit():
    with pytest.raises(HTTPException) as exc:
        await upload_module.prepare_upload(_request(credit_id=uuid.uuid4()))
    assert exc.value.status_code == 400


async def test_prepare_rejects_malformed_public_key():
    """Junk hex must be a 400, not an unhandled 500 (Keypair/fromhex raise)."""
    with pytest.raises(HTTPException) as exc:
        await upload_module.prepare_upload(_request(public_key="zz-not-hex", signature="also-not-hex"))
    assert exc.value.status_code == 400


async def test_prepare_rejects_banned_coldkey(monkeypatch):
    monkeypatch.setattr(
        upload_module,
        "check_coldkey_banned",
        AsyncMock(side_effect=HTTPException(status_code=403, detail="Your miner coldkey has been banned")),
    )
    with pytest.raises(HTTPException) as exc:
        await upload_module.prepare_upload(_request())
    assert exc.value.status_code == 403
    async with _db.pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM upload_payment_quotes WHERE miner_hotkey = $1", HOTKEY)
    assert count == 0


async def test_prepare_rejects_owner_hotkey(monkeypatch):
    """Owner uploads use team-upload, not tickets — a burn quote here would strand an irreversible
    burn behind a ticket that check/redeem always reject with owner_not_allowed."""
    monkeypatch.setattr(upload_module.config, "OWNER_HOTKEY", HOTKEY)
    with pytest.raises(HTTPException) as exc:
        await upload_module.prepare_upload(_request())
    assert exc.value.status_code == 400
    async with _db.pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM upload_payment_quotes WHERE miner_hotkey = $1", HOTKEY)
    assert count == 0


async def test_prepare_rejects_unregistered_hotkey(monkeypatch):
    monkeypatch.setattr(
        upload_module,
        "check_hotkey_registered",
        AsyncMock(side_effect=HTTPException(status_code=400, detail="Hotkey not registered on subnet")),
    )
    with pytest.raises(HTTPException) as exc:
        await upload_module.prepare_upload(_request())
    assert exc.value.status_code == 400
    async with _db.pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM upload_payment_quotes WHERE miner_hotkey = $1", HOTKEY)
    assert count == 0
