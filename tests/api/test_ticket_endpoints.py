import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from bittensor_wallet.keypair import Keypair
from fastapi import HTTPException

import utils.database as _db
from api.src.endpoints import upload as upload_module
from queries.competition import initialize_current_competition_policy
from queries.upload_credit import credit_payment_identity
from utils.upload_ticket import FUNDING_BURN, FUNDING_CREDIT, UploadTicket, encode_ticket, sign_ticket

KEYPAIR = Keypair.create_from_seed("0x" + "ab" * 32)
HOTKEY = KEYPAIR.ss58_address
OTHER_KEYPAIR = Keypair.create_from_seed("0x" + "cd" * 32)
FAKE_COLDKEY = "5FColdKey456"
FAKE_BLOCK_HASH = "0xdeadbeef1234"
FAKE_EXTRINSIC_INDEX = 1
FAKE_AMOUNT_ALPHA_RAO = 120_344_620_287_164
FAKE_BLOCK_TIME = datetime(2026, 6, 9, 18, 0, tzinfo=timezone.utc)

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


def _make_request() -> MagicMock:
    req = MagicMock()
    req.client.host = "127.0.0.1"
    return req


def _make_upload_file(content: bytes = b"async def agent_main(input): return 'ok'") -> MagicMock:
    f = MagicMock()
    f.filename = "agent.py"
    f.file = MagicMock()
    f.file.tell.return_value = len(content)
    chunks = [content, b""]
    f.read = AsyncMock(side_effect=chunks)
    f.seek = AsyncMock()
    return f


def _fake_timestamp_extrinsic() -> MagicMock:
    ext = MagicMock()
    ext.value_serialized = {
        "call": {
            "call_module": "Timestamp",
            "call_function": "set",
            "call_args": [{"name": "now", "value": int(FAKE_BLOCK_TIME.timestamp() * 1000)}],
        }
    }
    return ext


def _fake_burn_extrinsic() -> MagicMock:
    ext = MagicMock()
    ext.value_serialized = {
        "address": FAKE_COLDKEY,
        "call": {"call_module": "SubtensorModule", "call_function": "burn_alpha", "call_args": []},
    }
    return ext


@pytest.fixture(autouse=True)
def chain_and_s3_mocks(monkeypatch):
    monkeypatch.setattr(upload_module, "check_hotkey_registered", AsyncMock())
    monkeypatch.setattr(upload_module, "check_if_extrinsic_failed", AsyncMock(return_value=False))
    monkeypatch.setattr(
        upload_module.subtensor_client,
        "get_block_info",
        AsyncMock(
            return_value=SimpleNamespace(
                number=42,
                timestamp=int(FAKE_BLOCK_TIME.timestamp() * 1000),
                extrinsics=[_fake_timestamp_extrinsic(), _fake_burn_extrinsic()],
            )
        ),
    )
    monkeypatch.setattr(
        upload_module.subtensor_client,
        "get_events",
        AsyncMock(
            return_value=[
                {
                    "extrinsic_idx": FAKE_EXTRINSIC_INDEX,
                    "event": {
                        "module_id": "SubtensorModule",
                        "event_id": "AlphaBurned",
                        "attributes": (FAKE_COLDKEY, HOTKEY, FAKE_AMOUNT_ALPHA_RAO, upload_module.config.NETUID),
                    },
                }
            ]
        ),
    )
    monkeypatch.setattr(upload_module.subtensor_client, "get_hotkey_owner", AsyncMock(return_value=FAKE_COLDKEY))
    monkeypatch.setattr(upload_module, "upload_text_file_to_s3", AsyncMock())
    monkeypatch.setattr("queries.agent.upload_text_file_to_s3", AsyncMock())
    validated = MagicMock()
    validated.runtime_api_key = "fake-runtime-key"
    validated.management_api_key = "fake-management-key"
    validated.workspace_id = "fake-workspace-id"
    validated.api_key_label = "fake-label"
    validated.api_key_creator_user_id = "fake-creator-id"
    validated.validated_at = datetime.now(timezone.utc)
    monkeypatch.setattr(upload_module, "validate_openrouter_keys", AsyncMock(return_value=validated))


async def _insert_quote(hotkey: str = HOTKEY) -> uuid.UUID:
    quote_id = uuid.uuid4()
    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO upload_payment_quotes (quote_id, miner_hotkey, amount_alpha_rao, created_at, expires_at)
            VALUES ($1, $2, $3, $4, $5)
            """,
            quote_id,
            hotkey,
            FAKE_AMOUNT_ALPHA_RAO,
            FAKE_BLOCK_TIME - timedelta(minutes=1),
            FAKE_BLOCK_TIME + timedelta(minutes=15),
        )
    return quote_id


async def _insert_credit(hotkey: str = HOTKEY, expires_at: datetime | None = None, revoked: bool = False) -> uuid.UUID:
    credit_id = uuid.uuid4()
    granted_at = expires_at - timedelta(hours=1) if expires_at is not None else datetime.now(timezone.utc)
    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO upload_credits (credit_id, miner_hotkey, reason, granted_by, granted_at, expires_at, revoked_at, revoked_by)
            VALUES ($1, $2, 'test credit', 'pytest', $3, $4, $5, $6)
            """,
            credit_id,
            hotkey,
            granted_at,
            expires_at,
            datetime.now(timezone.utc) if revoked else None,
            "pytest" if revoked else None,
        )
    return credit_id


def _burn_ticket_blob(quote_id: uuid.UUID) -> str:
    unsigned = UploadTicket(
        hotkey=HOTKEY,
        public_key=KEYPAIR.public_key.hex(),
        funding=FUNDING_BURN,
        quote_id=str(quote_id),
        payment_block_hash=FAKE_BLOCK_HASH,
        payment_extrinsic_index=FAKE_EXTRINSIC_INDEX,
    )
    return encode_ticket(sign_ticket(unsigned, KEYPAIR.sign))


def _credit_ticket_blob(credit_id: uuid.UUID) -> str:
    unsigned = UploadTicket(
        hotkey=HOTKEY,
        public_key=KEYPAIR.public_key.hex(),
        funding=FUNDING_CREDIT,
        credit_id=str(credit_id),
    )
    return encode_ticket(sign_ticket(unsigned, KEYPAIR.sign))


async def _redeem(ticket_blob: str, content: bytes = b"async def agent_main(input): return 'ok'", set_id: int = 1):
    return await upload_module.post_agent_ticket(
        request=_make_request(),
        agent_file=_make_upload_file(content),
        ticket=ticket_blob,
        name="ticket-agent",
        openrouter_api_key="sk-or-v1-runtime",
        openrouter_management_key="sk-or-v1-management",
        set_id=set_id,
    )


async def test_burn_ticket_redeem_creates_agent():
    quote_id = await _insert_quote()

    response = await _redeem(_burn_ticket_blob(quote_id))

    assert response.status == "success"
    assert response.agent_id is not None
    assert response.miner_hotkey == HOTKEY
    assert response.miner_coldkey == FAKE_COLDKEY
    async with _db.pool.acquire() as conn:
        agent = await conn.fetchrow("SELECT miner_hotkey, name FROM agents WHERE miner_hotkey = $1", HOTKEY)
        payment = await conn.fetchrow(
            "SELECT agent_id FROM evaluation_payments WHERE payment_block_hash = $1", FAKE_BLOCK_HASH
        )
    assert agent["name"] == "ticket-agent"
    assert payment["agent_id"] is not None


async def test_burn_ticket_replay_is_rejected():
    quote_id = await _insert_quote()
    blob = _burn_ticket_blob(quote_id)
    await _redeem(blob)

    with pytest.raises(HTTPException) as exc:
        await _redeem(blob)
    assert exc.value.status_code == 402
    assert exc.value.detail == "Agent ID already exists for this payment information."


async def test_burn_ticket_cannot_redeem_other_hotkeys_quote():
    """A ticket signed by hotkey A must not be able to spend hotkey B's burn quote."""
    quote_id = await _insert_quote(hotkey=OTHER_KEYPAIR.ss58_address)

    with pytest.raises(HTTPException) as exc:
        await _redeem(_burn_ticket_blob(quote_id))
    assert exc.value.status_code == 402
    assert exc.value.detail == "Payment quote does not match upload hotkey"


async def test_credit_ticket_redeem_consumes_credit():
    credit_id = await _insert_credit()

    response = await _redeem(_credit_ticket_blob(credit_id))

    assert response.status == "success"
    payment_block_hash, _ = credit_payment_identity(credit_id)
    async with _db.pool.acquire() as conn:
        credit = await conn.fetchrow(
            "SELECT redeemed_at, redeemed_agent_id FROM upload_credits WHERE credit_id = $1", credit_id
        )
        payment = await conn.fetchrow(
            "SELECT amount_alpha_rao, upload_credit_id FROM evaluation_payments WHERE payment_block_hash = $1",
            payment_block_hash,
        )
    assert credit["redeemed_at"] is not None
    assert payment["amount_alpha_rao"] == 0
    assert payment["upload_credit_id"] == credit_id
    upload_module.subtensor_client.get_block_info.assert_not_awaited()


async def test_credit_ticket_same_file_retry_is_idempotent():
    credit_id = await _insert_credit()
    blob = _credit_ticket_blob(credit_id)
    await _redeem(blob)

    response = await _redeem(blob)

    assert response.status == "success"
    assert "already used" in response.message
    async with _db.pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM agents WHERE miner_hotkey = $1", HOTKEY)
    assert count == 1


async def test_credit_ticket_different_file_retry_conflicts():
    credit_id = await _insert_credit()
    blob = _credit_ticket_blob(credit_id)
    await _redeem(blob)

    with pytest.raises(HTTPException) as exc:
        await _redeem(blob, content=b"async def agent_main(input): return 'different'")
    assert exc.value.status_code == 409


async def test_credit_ticket_cannot_redeem_other_hotkeys_credit():
    """A ticket signed by hotkey A must not be able to spend hotkey B's upload credit."""
    credit_id = await _insert_credit(hotkey=OTHER_KEYPAIR.ss58_address)

    with pytest.raises(HTTPException) as exc:
        await _redeem(_credit_ticket_blob(credit_id))
    assert exc.value.status_code == 402
    assert exc.value.detail == "Upload credit is not available for this hotkey"


async def test_malformed_ticket_is_400():
    with pytest.raises(HTTPException) as exc:
        await _redeem("garbage-not-base64")
    assert exc.value.status_code == 400
    assert exc.value.detail == "malformed_ticket"


async def test_tampered_signature_is_400():
    quote_id = await _insert_quote()
    unsigned = UploadTicket(
        hotkey=HOTKEY,
        public_key=KEYPAIR.public_key.hex(),
        funding=FUNDING_BURN,
        quote_id=str(quote_id),
        payment_block_hash="0xATTACKER",
        payment_extrinsic_index=FAKE_EXTRINSIC_INDEX,
    )
    honest = sign_ticket(
        UploadTicket(
            hotkey=HOTKEY,
            public_key=KEYPAIR.public_key.hex(),
            funding=FUNDING_BURN,
            quote_id=str(quote_id),
            payment_block_hash=FAKE_BLOCK_HASH,
            payment_extrinsic_index=FAKE_EXTRINSIC_INDEX,
        ),
        KEYPAIR.sign,
    )
    import dataclasses

    tampered = dataclasses.replace(unsigned, signature=honest.signature)

    with pytest.raises(HTTPException) as exc:
        await _redeem(encode_ticket(tampered))
    assert exc.value.status_code == 400
    assert exc.value.detail == "invalid_signature"
    async with _db.pool.acquire() as conn:
        recorded = await conn.fetchval(
            "SELECT COUNT(*) FROM upload_attempts WHERE success = false AND error_message = 'invalid_signature'"
        )
    assert recorded == 1


async def test_owner_tickets_are_rejected(monkeypatch):
    """Owner uploads bypass funding checks in the shared core — tickets must refuse them."""
    monkeypatch.setattr(upload_module.config, "OWNER_HOTKEY", HOTKEY)
    quote_id = await _insert_quote()

    with pytest.raises(HTTPException) as exc:
        await _redeem(_burn_ticket_blob(quote_id))
    assert exc.value.status_code == 400
    assert exc.value.detail == "owner_not_allowed"
    async with _db.pool.acquire() as conn:
        recorded = await conn.fetchval(
            "SELECT COUNT(*) FROM upload_attempts WHERE success = false AND error_message = 'owner_not_allowed'"
        )
    assert recorded == 1


# ── ticket-check endpoint ─────────────────────────────────────────────────────


async def _check(ticket_blob: str):
    from models.upload import TicketCheckRequest

    return await upload_module.check_ticket(TicketCheckRequest(ticket=ticket_blob))


async def test_check_valid_burn_ticket(monkeypatch):
    monkeypatch.setattr(
        upload_module,
        "_resolve_upload_set_id",
        AsyncMock(side_effect=AssertionError("ticket-check must remain competition-free")),
    )
    quote_id = await _insert_quote()
    result = await _check(_burn_ticket_blob(quote_id))
    assert result.valid is True
    assert result.reason is None
    assert result.hotkey == HOTKEY
    assert result.funding == "burn"
    assert result.amount_alpha_rao == FAKE_AMOUNT_ALPHA_RAO
    assert result.expires_at is None


async def test_check_malformed_and_bad_signature():
    malformed = await _check("junk")
    assert (malformed.valid, malformed.reason) == (False, "malformed_ticket")

    quote_id = await _insert_quote()
    import base64
    import json

    payload = json.loads(base64.b64decode(_burn_ticket_blob(quote_id)))
    payload["payment_block_hash"] = "0xtampered"
    tampered = base64.b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).decode()
    bad_sig = await _check(tampered)
    assert (bad_sig.valid, bad_sig.reason) == (False, "invalid_signature")


async def test_check_unknown_quote():
    result = await _check(_burn_ticket_blob(uuid.uuid4()))
    assert (result.valid, result.reason) == (False, "unknown_quote")


async def test_check_redeemed_burn_ticket_reports_agent():
    quote_id = await _insert_quote()
    blob = _burn_ticket_blob(quote_id)
    await _redeem(blob)

    result = await _check(blob)
    assert (result.valid, result.reason) == (False, "already_redeemed")
    assert result.redeemed_agent_id is not None


async def test_check_burn_ticket_reserved_under_different_quote():
    """A payment already reserved (agent_id NULL) under a DIFFERENT quote must report
    unknown_quote, not valid — redeem would deterministically 409 on this ticket, so check must
    agree rather than falsely advertising it as redeemable."""
    quote_a = await _insert_quote()
    quote_b = await _insert_quote()
    async with _db.pool.acquire() as conn:
        # Column list mirrors the reserve-row fixture in tests/api/test_upload.py::test_partial_failure_retry_succeeds.
        await conn.execute(
            """
            INSERT INTO evaluation_payments
                (payment_block_hash, payment_extrinsic_index, agent_id, miner_hotkey, miner_coldkey, amount_alpha_rao, quote_id)
            VALUES ($1, $2, NULL, $3, $4, $5, $6)
            """,
            FAKE_BLOCK_HASH,
            str(FAKE_EXTRINSIC_INDEX),
            HOTKEY,
            FAKE_COLDKEY,
            FAKE_AMOUNT_ALPHA_RAO,
            quote_b,
        )

    result = await _check(_burn_ticket_blob(quote_a))
    assert (result.valid, result.reason) == (False, "unknown_quote")


async def test_check_refunded_burn_ticket():
    # Column list copied from the proven fixture in tests/api/test_upload.py::test_refunded_payment_raises_402.
    quote_id = await _insert_quote()
    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO failed_upload_refunds
                (id, block_hash, block_extrinsic_index, amount, tx_hash, upload_tx_hash, upload_block_hash, upload_block_extrinsic_index, coldkey, upload_amount)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
            uuid.uuid4(),
            "0xrefundblock",
            "1",
            FAKE_AMOUNT_ALPHA_RAO,
            "0xrefundtxhash",
            "0xuploadtxhash",
            FAKE_BLOCK_HASH,
            str(FAKE_EXTRINSIC_INDEX),
            FAKE_COLDKEY,
            FAKE_AMOUNT_ALPHA_RAO,
        )
    result = await _check(_burn_ticket_blob(quote_id))
    assert (result.valid, result.reason) == (False, "refunded")


async def test_check_valid_credit_ticket_with_expiry():
    expires_at = datetime.now(timezone.utc) + timedelta(days=3)
    credit_id = await _insert_credit(expires_at=expires_at)
    result = await _check(_credit_ticket_blob(credit_id))
    assert result.valid is True
    assert result.funding == "credit"
    assert result.amount_alpha_rao == 0
    assert result.expires_at is not None


async def test_check_credit_states():
    unknown = await _check(_credit_ticket_blob(uuid.uuid4()))
    assert (unknown.valid, unknown.reason) == (False, "unknown_credit")

    revoked_id = await _insert_credit(revoked=True)
    revoked = await _check(_credit_ticket_blob(revoked_id))
    assert (revoked.valid, revoked.reason) == (False, "credit_revoked")

    expired_id = await _insert_credit(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    expired = await _check(_credit_ticket_blob(expired_id))
    assert (expired.valid, expired.reason) == (False, "credit_expired")

    redeemed_id = await _insert_credit()
    await _redeem(_credit_ticket_blob(redeemed_id))
    redeemed = await _check(_credit_ticket_blob(redeemed_id))
    assert (redeemed.valid, redeemed.reason) == (False, "already_redeemed")
    assert redeemed.redeemed_agent_id is not None


async def test_check_owner_ticket_not_allowed(monkeypatch):
    monkeypatch.setattr(upload_module.config, "OWNER_HOTKEY", HOTKEY)
    quote_id = await _insert_quote()
    result = await _check(_burn_ticket_blob(quote_id))
    assert (result.valid, result.reason) == (False, "owner_not_allowed")


# ── OpenRouter key validation endpoint ────────────────────────────────────────


async def test_validate_openrouter_keys_valid():
    from models.upload import OpenRouterKeysCheckRequest

    result = await upload_module.validate_openrouter_keys_endpoint(
        OpenRouterKeysCheckRequest(openrouter_api_key="sk-or-v1-a", openrouter_management_key="sk-or-v1-b")
    )
    assert result.valid is True
    assert result.reason is None


async def test_validate_openrouter_keys_invalid(monkeypatch):
    from models.upload import OpenRouterKeysCheckRequest

    monkeypatch.setattr(
        upload_module,
        "validate_openrouter_keys",
        AsyncMock(side_effect=HTTPException(status_code=400, detail="Invalid OpenRouter API key")),
    )
    result = await upload_module.validate_openrouter_keys_endpoint(
        OpenRouterKeysCheckRequest(openrouter_api_key="bad", openrouter_management_key="bad")
    )
    assert result.valid is False
    assert result.reason == "Invalid OpenRouter API key"


async def test_validate_openrouter_keys_outage_stays_503(monkeypatch):
    from models.upload import OpenRouterKeysCheckRequest

    monkeypatch.setattr(
        upload_module,
        "validate_openrouter_keys",
        AsyncMock(side_effect=HTTPException(status_code=503, detail="OpenRouter unreachable")),
    )
    with pytest.raises(HTTPException) as exc:
        await upload_module.validate_openrouter_keys_endpoint(
            OpenRouterKeysCheckRequest(openrouter_api_key="a", openrouter_management_key="b")
        )
    assert exc.value.status_code == 503
