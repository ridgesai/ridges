import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from bittensor_wallet.keypair import Keypair
from fastapi import HTTPException

import utils.database as _db
from api.src.endpoints import upload as upload_module
from models.upload import CancelQuoteRequest, ConfirmPaymentRequest
from queries.competition import initialize_current_competition_policy
from utils.upload_pricing import PricingSettings, multiplier
from utils.upload_ticket import cancel_signing_string, confirm_signing_string

KEYPAIR = Keypair.create_from_seed("0x" + "cd" * 32)
HOTKEY = KEYPAIR.ss58_address
OTHER = Keypair.create_from_seed("0x" + "ef" * 32)
COLDKEY = "5FColdKey456"
BLOCK_HASH = "0x" + "ab" * 32
OTHER_BLOCK_HASH = "0x" + "bc" * 32
EXTRINSIC_INDEX = 1
BURN_RAO = 3_000_000_000
QUOTE_RAO = 2_000_000_000
BLOCK_TIME = datetime.now(timezone.utc)
M = multiplier(PricingSettings())

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
            "TRUNCATE evaluation_payments, upload_payment_quotes, agents, competitions RESTART IDENTITY CASCADE"
        )
        await conn.execute("INSERT INTO competitions (set_id, start_date) VALUES (1, NOW())")
    await initialize_current_competition_policy()
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE evaluation_payments, upload_payment_quotes, agents, competitions RESTART IDENTITY CASCADE"
        )


@pytest.fixture(autouse=True)
def chain_mocks(monkeypatch):
    burn_ext = MagicMock()
    burn_ext.value_serialized = {
        "address": COLDKEY,
        "call": {"call_module": "SubtensorModule", "call_function": "burn_alpha", "call_args": []},
    }
    monkeypatch.setattr(upload_module, "check_if_extrinsic_failed", AsyncMock(return_value=False))
    monkeypatch.setattr(
        upload_module.subtensor_client,
        "get_block_info",
        AsyncMock(
            return_value=SimpleNamespace(
                number=42, timestamp=int(BLOCK_TIME.timestamp() * 1000), extrinsics=[MagicMock(), burn_ext]
            )
        ),
    )
    monkeypatch.setattr(
        upload_module.subtensor_client,
        "get_events",
        AsyncMock(
            return_value=[
                {
                    "extrinsic_idx": EXTRINSIC_INDEX,
                    "event": {
                        "module_id": "SubtensorModule",
                        "event_id": "AlphaBurned",
                        "attributes": (COLDKEY, HOTKEY, BURN_RAO, upload_module.config.NETUID),
                    },
                }
            ]
        ),
    )
    monkeypatch.setattr(upload_module.subtensor_client, "get_hotkey_owner", AsyncMock(return_value=COLDKEY))


async def _insert_quote(
    *, legacy: bool = False, created_at: datetime | None = None, expires_at: datetime | None = None
) -> uuid.UUID:
    quote_id = uuid.uuid4()
    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO upload_payment_quotes
                (quote_id, miner_hotkey, amount_alpha_rao, created_at, expires_at, set_id, price_usd, miner_coldkey,
                 is_legacy)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            """,
            quote_id,
            HOTKEY,
            QUOTE_RAO,
            created_at or BLOCK_TIME - timedelta(minutes=1),
            expires_at or BLOCK_TIME + timedelta(minutes=14),
            None if legacy else 1,
            None if legacy else 5,
            None if legacy else COLDKEY,
            legacy,
        )
    return quote_id


def _confirm_request(
    quote_id: uuid.UUID, *, keypair: Keypair = KEYPAIR, block_hash: str = BLOCK_HASH
) -> ConfirmPaymentRequest:
    message = confirm_signing_string(keypair.ss58_address, str(quote_id), block_hash, str(EXTRINSIC_INDEX))
    return ConfirmPaymentRequest(
        quote_id=quote_id,
        payment_block_hash=block_hash,
        payment_extrinsic_index=EXTRINSIC_INDEX,
        hotkey=keypair.ss58_address,
        public_key=keypair.public_key.hex(),
        signature=keypair.sign(message).hex(),
    )


def _cancel_request(quote_id: uuid.UUID, *, keypair: Keypair = KEYPAIR) -> CancelQuoteRequest:
    message = cancel_signing_string(keypair.ss58_address, str(quote_id))
    return CancelQuoteRequest(
        hotkey=keypair.ss58_address, public_key=keypair.public_key.hex(), signature=keypair.sign(message).hex()
    )


async def _price() -> float | None:
    async with _db.pool.acquire() as conn:
        return await conn.fetchval("SELECT price_usd::float8 FROM competition_upload_prices WHERE set_id = 1")


async def _payment_rows() -> int:
    async with _db.pool.acquire() as conn:
        return await conn.fetchval("SELECT COUNT(*) FROM evaluation_payments")


async def test_confirm_records_payment_and_bumps_once():
    quote_id = await _insert_quote()
    response = await upload_module.confirm_payment(_confirm_request(quote_id))
    assert response.status == "confirmed"
    assert await _price() == pytest.approx(5 * M, rel=1e-3)
    async with _db.pool.acquire() as conn:
        payment = await conn.fetchrow("SELECT * FROM evaluation_payments")
        quote = await conn.fetchrow("SELECT * FROM upload_payment_quotes WHERE quote_id = $1", quote_id)
    assert payment["agent_id"] is None
    assert payment["quote_id"] == quote_id
    assert payment["miner_coldkey"] == COLDKEY
    assert payment["amount_alpha_rao"] == BURN_RAO
    assert quote["confirmed_payment_block_hash"] == BLOCK_HASH


async def test_replay_with_same_receipt_is_idempotent_even_after_deadline_and_chain_down(monkeypatch):
    quote_id = await _insert_quote()
    await upload_module.confirm_payment(_confirm_request(quote_id))
    async with _db.pool.acquire() as conn:
        await conn.execute("UPDATE upload_payment_quotes SET expires_at = NOW() - interval '3 hours'")
    monkeypatch.setattr(upload_module.subtensor_client, "get_block_info", AsyncMock(side_effect=RuntimeError("down")))
    response = await upload_module.confirm_payment(_confirm_request(quote_id))
    assert response.status == "replayed"
    assert await _price() == pytest.approx(5 * M, rel=1e-3)


async def test_different_receipt_conflicts_without_bump():
    quote_id = await _insert_quote()
    await upload_module.confirm_payment(_confirm_request(quote_id))
    with pytest.raises(HTTPException) as exc:
        await upload_module.confirm_payment(_confirm_request(quote_id, block_hash=OTHER_BLOCK_HASH))
    assert (exc.value.status_code, exc.value.detail) == (409, "receipt_conflict")
    assert await _price() == pytest.approx(5 * M, rel=1e-3)


async def test_one_burn_cannot_confirm_two_quotes():
    first = await _insert_quote()
    await upload_module.confirm_payment(_confirm_request(first))
    second = await _insert_quote()
    with pytest.raises(HTTPException) as exc:
        await upload_module.confirm_payment(_confirm_request(second))
    assert (exc.value.status_code, exc.value.detail) == (409, "receipt_conflict")
    assert await _payment_rows() == 1


async def test_cancelled_quote_cannot_be_confirmed():
    quote_id = await _insert_quote()
    assert (await upload_module.cancel_quote(quote_id, _cancel_request(quote_id))).status == "cancelled"
    with pytest.raises(HTTPException) as exc:
        await upload_module.confirm_payment(_confirm_request(quote_id))
    assert (exc.value.status_code, exc.value.detail) == (409, "quote_cancelled")
    assert await _payment_rows() == 0


async def test_confirmed_quote_cannot_be_cancelled():
    quote_id = await _insert_quote()
    await upload_module.confirm_payment(_confirm_request(quote_id))
    with pytest.raises(HTTPException) as exc:
        await upload_module.cancel_quote(quote_id, _cancel_request(quote_id))
    assert (exc.value.status_code, exc.value.detail) == (409, "already_confirmed")


async def test_cancel_is_idempotent():
    quote_id = await _insert_quote()
    await upload_module.cancel_quote(quote_id, _cancel_request(quote_id))
    assert (await upload_module.cancel_quote(quote_id, _cancel_request(quote_id))).status == "cancelled"


async def test_concurrent_cancel_and_confirm_exactly_one_wins():
    quote_id = await _insert_quote()
    results = await asyncio.gather(
        upload_module.confirm_payment(_confirm_request(quote_id)),
        upload_module.cancel_quote(quote_id, _cancel_request(quote_id)),
        return_exceptions=True,
    )
    failures = [result for result in results if isinstance(result, HTTPException)]
    assert len(failures) == 1 and failures[0].status_code == 409
    async with _db.pool.acquire() as conn:
        row = await conn.fetchrow("SELECT confirmed_at, cancelled_at FROM upload_payment_quotes")
    assert (row["confirmed_at"] is None) != (row["cancelled_at"] is None)


async def test_first_confirm_after_deadline_is_rejected(monkeypatch):
    quote_id = await _insert_quote(
        created_at=BLOCK_TIME - timedelta(hours=3), expires_at=BLOCK_TIME - timedelta(hours=2)
    )

    async def _burned_inside_its_window(**_kwargs):
        return upload_module.VerifiedBurn(miner_coldkey=COLDKEY, amount_alpha_rao=BURN_RAO)

    monkeypatch.setattr(upload_module, "_verify_burn_on_chain", _burned_inside_its_window)
    with pytest.raises(HTTPException) as exc:
        await upload_module.confirm_payment(_confirm_request(quote_id))
    assert (exc.value.status_code, exc.value.detail) == (402, "burn_not_reported")
    assert await _price() is None


async def test_first_confirm_rejected_when_verification_crosses_deadline(monkeypatch):
    # The request starts on time and becomes late during verification: the deadline is judged after the lock.
    async with _db.pool.acquire() as conn:
        db_now = await conn.fetchval("SELECT clock_timestamp()")
    quote_id = await _insert_quote(
        created_at=db_now - timedelta(hours=2),
        expires_at=db_now - timedelta(hours=1) + timedelta(seconds=2),
    )
    verified: list[bool] = []

    async def _slow_verification(**_kwargs):
        verified.append(True)
        await asyncio.sleep(3)
        return upload_module.VerifiedBurn(miner_coldkey=COLDKEY, amount_alpha_rao=BURN_RAO)

    monkeypatch.setattr(upload_module, "_verify_burn_on_chain", _slow_verification)
    with pytest.raises(HTTPException) as exc:
        await upload_module.confirm_payment(_confirm_request(quote_id))
    assert verified == [True]
    assert (exc.value.status_code, exc.value.detail) == (402, "burn_not_reported")
    assert await _payment_rows() == 0
    assert await _price() is None


async def test_other_hotkey_cannot_confirm_or_cancel():
    # Confirmed first, so a replay answer must not leak to another hotkey either.
    quote_id = await _insert_quote()
    await upload_module.confirm_payment(_confirm_request(quote_id))
    with pytest.raises(HTTPException) as exc:
        await upload_module.confirm_payment(_confirm_request(quote_id, keypair=OTHER))
    assert (exc.value.status_code, exc.value.detail) == (403, "not_quote_owner")
    with pytest.raises(HTTPException) as exc:
        await upload_module.cancel_quote(quote_id, _cancel_request(quote_id, keypair=OTHER))
    assert (exc.value.status_code, exc.value.detail) == (403, "not_quote_owner")


async def test_legacy_quote_confirm_and_cancel_are_no_ops():
    quote_id = await _insert_quote(legacy=True)
    assert (await upload_module.confirm_payment(_confirm_request(quote_id))).status == "legacy"
    assert (await upload_module.cancel_quote(quote_id, _cancel_request(quote_id))).status == "legacy"
    assert await _payment_rows() == 0
    assert await _price() is None


async def test_confirm_succeeds_after_competition_closes():
    quote_id = await _insert_quote()
    async with _db.pool.acquire() as conn:
        await conn.execute("UPDATE competitions SET submissions_closed_at = NOW(), emissions_end_at = NOW()")
    assert (await upload_module.confirm_payment(_confirm_request(quote_id))).status == "confirmed"
    assert await _price() == pytest.approx(5 * M, rel=1e-3)


async def test_non_prod_confirm_bumps_without_chain_or_payment_row(monkeypatch):
    monkeypatch.setattr(upload_module.config, "ENV", "dev")
    monkeypatch.setattr(upload_module.subtensor_client, "get_block_info", AsyncMock(side_effect=AssertionError))
    quote_id = await _insert_quote()
    assert (await upload_module.confirm_payment(_confirm_request(quote_id))).status == "confirmed"
    assert await _payment_rows() == 0
    assert await _price() == pytest.approx(5 * M, rel=1e-3)


async def test_bad_receipt_is_400():
    quote_id = await _insert_quote()
    request = _confirm_request(quote_id).model_copy(update={"payment_extrinsic_index": "+1"})
    with pytest.raises(HTTPException) as exc:
        await upload_module.confirm_payment(request)
    assert exc.value.status_code == 400


async def test_api_clock_ahead_of_database_does_not_forfeit_a_timely_confirm(monkeypatch):
    # 30 seconds remain on the database clock, but this API host runs 60 seconds fast.
    async with _db.pool.acquire() as conn:
        db_now = await conn.fetchval("SELECT clock_timestamp()")
    quote_id = await _insert_quote(
        created_at=db_now - timedelta(hours=2),
        expires_at=db_now - timedelta(hours=1) + timedelta(seconds=30),
    )

    class _FastClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(seconds=60)

    async def _verified(**_kwargs):
        return upload_module.VerifiedBurn(miner_coldkey=COLDKEY, amount_alpha_rao=BURN_RAO)

    monkeypatch.setattr(upload_module, "datetime", _FastClock)
    monkeypatch.setattr(upload_module, "_verify_burn_on_chain", _verified)
    assert (await upload_module.confirm_payment(_confirm_request(quote_id))).status == "confirmed"
