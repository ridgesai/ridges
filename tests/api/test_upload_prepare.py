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
from queries.competition import initialize_current_competition_policy
from utils.upload_pricing import alpha_rao_for_usd
from utils.upload_ticket import prepare_signing_string

KEYPAIR = Keypair.create_from_seed("0x" + "ab" * 32)
HOTKEY = KEYPAIR.ss58_address
FAKE_COLDKEY = "5FColdKey456"
FAKE_AMOUNT_ALPHA_RAO = 120_344_620_287_164
FAKE_ALPHA_PRICE_USD = 2.5
EXPECTED_QUOTE_ALPHA_RAO = alpha_rao_for_usd(5.0, FAKE_ALPHA_PRICE_USD)

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module", autouse=True)
def upload_prod_mode():
    original_env = upload_module.config.ENV
    upload_module.config.ENV = "prod"
    yield
    upload_module.config.ENV = original_env


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db):
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE evaluation_payments, upload_credits, upload_payment_quotes, agents, banned_coldkeys, "
            "failed_upload_refunds, upload_attempts, evaluation_sets, competitions RESTART IDENTITY CASCADE"
        )
        await conn.execute("INSERT INTO competitions (set_id, start_date) VALUES (1, NOW())")
    await initialize_current_competition_policy()
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE evaluation_payments, upload_credits, upload_payment_quotes, agents, banned_coldkeys, "
            "failed_upload_refunds, upload_attempts, evaluation_sets, competitions RESTART IDENTITY CASCADE"
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
    monkeypatch.setattr(upload_module, "get_alpha_price_usd", AsyncMock(return_value=FAKE_ALPHA_PRICE_USD))


def _request(**overrides) -> PrepareUploadRequest:
    fields = {
        "hotkey": HOTKEY,
        "public_key": KEYPAIR.public_key.hex(),
        "signature": KEYPAIR.sign(prepare_signing_string(HOTKEY)).hex(),
        "use_credit": False,
        "credit_id": None,
        "set_id": 1,
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
    assert response.amount_alpha_rao == EXPECTED_QUOTE_ALPHA_RAO
    assert response.payment_netuid == upload_module.config.NETUID
    assert response.expires_at is not None
    assert "set_id" not in response.model_dump()
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


async def test_burn_prepare_does_not_enforce_competition_rate_limit(monkeypatch):
    monkeypatch.setattr(
        upload_module,
        "get_latest_agent_created_at_for_miner_hotkey_in_competition",
        AsyncMock(side_effect=AssertionError("prepare-upload must not enforce the upload cooldown")),
    )
    response = await upload_module.prepare_upload(_request())
    assert response.payment_method == "burn"


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
        "get_latest_agent_created_at_for_miner_hotkey_in_competition",
        AsyncMock(side_effect=AssertionError("prepare-upload must remain competition-free")),
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


SECOND_KEYPAIR = Keypair.create_from_seed("0x" + "cd" * 32)


def _second_request(**overrides) -> PrepareUploadRequest:
    hotkey = SECOND_KEYPAIR.ss58_address
    fields = {
        "hotkey": hotkey,
        "public_key": SECOND_KEYPAIR.public_key.hex(),
        "signature": SECOND_KEYPAIR.sign(prepare_signing_string(hotkey)).hex(),
        "use_credit": False,
        "credit_id": None,
        "set_id": 1,
    }
    fields.update(overrides)
    return PrepareUploadRequest(**fields)


async def test_burn_prepare_without_set_id_asks_for_upgrade():
    with pytest.raises(HTTPException) as exc:
        await upload_module.prepare_upload(_request(set_id=None))
    assert exc.value.status_code == 400
    assert exc.value.detail == upload_module.OUTDATED_UPLOAD_CLIENT_MESSAGE


async def test_credit_prepare_needs_no_competition():
    await _insert_credit()
    response = await upload_module.prepare_upload(_request(use_credit=True, set_id=None))
    assert response.payment_method == "credit"


async def test_burn_quote_is_bound_to_competition_and_price():
    response = await upload_module.prepare_upload(_request())
    assert response.price_usd == pytest.approx(5.0)
    async with _db.pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM upload_payment_quotes WHERE quote_id = $1", response.quote_id)
    assert row["set_id"] == 1
    assert float(row["price_usd"]) == pytest.approx(5.0)
    assert row["miner_coldkey"] == FAKE_COLDKEY
    assert row["is_legacy"] is False
    assert (row["expires_at"] - row["created_at"]).total_seconds() == 15 * 60


async def test_same_hotkey_gets_its_open_quote_back():
    first = await upload_module.prepare_upload(_request())
    second = await upload_module.prepare_upload(_request())
    assert second.quote_id == first.quote_id


async def test_other_hotkey_of_same_coldkey_gets_409():
    first = await upload_module.prepare_upload(_request())
    with pytest.raises(HTTPException) as exc:
        await upload_module.prepare_upload(_second_request())
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "open_quote_exists"
    assert exc.value.detail["quote_id"] == str(first.quote_id)


async def test_quote_uses_current_competition_price():
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO competition_upload_prices (set_id, price_usd, price_updated_at) "
            "VALUES (1, 20, clock_timestamp())"
        )
    response = await upload_module.prepare_upload(_request())
    assert response.price_usd == pytest.approx(20.0, rel=1e-3)
    assert response.amount_alpha_rao == pytest.approx(alpha_rao_for_usd(20.0, FAKE_ALPHA_PRICE_USD), rel=1e-3)


async def test_eval_pricing_defaults_without_writing():
    response = await upload_module.get_upload_price(set_id=1)
    assert response.price_usd == 5.0
    assert response.floor_usd == 5.0
    assert response.target_per_hour == 10.0
    assert response.half_life_minutes == 30.0
    assert response.multiplier == pytest.approx(2**0.2)
    assert response.amount_alpha_rao == EXPECTED_QUOTE_ALPHA_RAO
    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM competition_upload_prices") == 0


async def test_eval_pricing_unknown_competition_is_404():
    with pytest.raises(HTTPException) as exc:
        await upload_module.get_upload_price(set_id=999)
    assert exc.value.status_code == 404
