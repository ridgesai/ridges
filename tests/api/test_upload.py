"""
Integration tests for the idempotent upload/payment flow.

All DB calls are real (Postgres via testcontainer). Blockchain and S3 are mocked.
One container starts per module; tables are truncated between tests.
"""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import utils.database as _db
from api.src.endpoints import upload as upload_module
from api.src.endpoints.upload import AgentUploadResponse
from queries.agent import _derive_agent_id
from queries.payments import retrieve_payment_by_hash

# ── constants ─────────────────────────────────────────────────────────────────

FAKE_BLOCK_HASH = "0xdeadbeef1234"
FAKE_EXTRINSIC_INDEX = "1"
FAKE_HOTKEY = "5FHneTesthKey123"
FAKE_COLDKEY = "5FColdKey456"
FAKE_AMOUNT_ALPHA_RAO = 120_344_620_287_164
FAKE_OWNER_HOTKEY = upload_module.config.OWNER_HOTKEY
FAKE_BLOCK_TIME = datetime(2026, 6, 9, 18, 0, tzinfo=timezone.utc)

# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module", autouse=True)
def upload_prod_mode():
    """Run all tests in this module against the prod code path."""
    original_env = upload_module.config.ENV
    upload_module.config.ENV = "prod"
    yield
    upload_module.config.ENV = original_env


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db):
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE evaluation_payments, upload_payment_quotes, agents, failed_upload_refunds, upload_attempts RESTART IDENTITY CASCADE"
        )


@pytest.fixture(autouse=True)
def blockchain_and_s3_mocks(monkeypatch):
    _install_mocks(monkeypatch)


# ── helpers ───────────────────────────────────────────────────────────────────


def _deterministic_id() -> uuid.UUID:
    return _derive_agent_id(FAKE_BLOCK_HASH, FAKE_EXTRINSIC_INDEX)


def _make_request() -> MagicMock:
    req = MagicMock()
    req.client.host = "127.0.0.1"
    return req


def _make_upload_file(
    content: bytes = b"async def agent_main(input): return 'ok'",
) -> MagicMock:
    f = MagicMock()
    f.filename = "agent.py"
    f.file = MagicMock()
    f.file.tell.return_value = len(content)
    _CHUNK_SIZE = 1024 * 1024
    chunks = [content[i : i + _CHUNK_SIZE] for i in range(0, len(content), _CHUNK_SIZE)] if content else []
    chunks.append(b"")
    f.read = AsyncMock(side_effect=chunks)
    f.seek = AsyncMock()
    return f


def _make_fake_timestamp_extrinsic() -> MagicMock:
    ext = MagicMock()
    ext.value_serialized = {
        "call": {
            "call_module": "Timestamp",
            "call_function": "set",
            "call_args": [
                {"name": "now", "value": int(FAKE_BLOCK_TIME.timestamp() * 1000)},
            ],
        }
    }
    return ext


def _make_fake_burn_extrinsic(coldkey: str) -> MagicMock:
    ext = MagicMock()
    ext.value_serialized = {
        "address": coldkey,
        "call": {"call_module": "SubtensorModule", "call_function": "burn_alpha", "call_args": []},
    }
    return ext


def _fake_events(extrinsic_idx: int, coldkey: str, netuid: int, amount: int) -> list:
    return [
        {
            "extrinsic_idx": extrinsic_idx,
            "event": {
                "module_id": "SubtensorModule",
                "event_id": "AlphaBurned",
                "attributes": {
                    "Coldkey": coldkey,
                    "Hotkey": FAKE_HOTKEY,
                    "Actual Alpha Decrease": amount,
                    "Netuid": netuid,
                },
            },
        }
    ]


def _install_mocks(monkeypatch) -> None:
    """Patch blockchain + S3. prod flag is set by upload_prod_mode."""
    monkeypatch.setattr(upload_module, "check_signature", MagicMock())
    monkeypatch.setattr(upload_module, "check_hotkey_registered", AsyncMock())
    monkeypatch.setattr(upload_module, "check_agent_banned", AsyncMock())
    monkeypatch.setattr(
        upload_module,
        "check_if_extrinsic_failed",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        upload_module.subtensor_client,
        "get_block_info",
        AsyncMock(
            return_value=SimpleNamespace(
                number=42,
                timestamp=int(FAKE_BLOCK_TIME.timestamp() * 1000),
                extrinsics=[
                    _make_fake_timestamp_extrinsic(),
                    _make_fake_burn_extrinsic(FAKE_COLDKEY),
                ],
            )
        ),
    )
    monkeypatch.setattr(
        upload_module.subtensor_client,
        "get_events",
        AsyncMock(return_value=_fake_events(1, FAKE_COLDKEY, upload_module.SN62_NETUID, FAKE_AMOUNT_ALPHA_RAO)),
    )
    monkeypatch.setattr(
        upload_module.subtensor_client,
        "get_hotkey_owner",
        AsyncMock(return_value=FAKE_COLDKEY),
    )
    monkeypatch.setattr(
        upload_module.subtensor_client,
        "get_alpha_stake",
        AsyncMock(return_value=MagicMock(rao=FAKE_AMOUNT_ALPHA_RAO * 10)),
    )
    monkeypatch.setattr(
        upload_module,
        "get_upload_price",
        AsyncMock(return_value=MagicMock(amount_alpha_rao=FAKE_AMOUNT_ALPHA_RAO)),
    )
    monkeypatch.setattr("queries.agent.upload_text_file_to_s3", AsyncMock())
    response_validate_open_router_keys = MagicMock()
    response_validate_open_router_keys.runtime_api_key = "fake-runtime-key"
    response_validate_open_router_keys.management_api_key = "fake-management-key"
    response_validate_open_router_keys.workspace_id = "fake-workspace-id"
    response_validate_open_router_keys.api_key_label = "fake-label"
    response_validate_open_router_keys.api_key_creator_user_id = "fake-creator-id"
    response_validate_open_router_keys.validated_at = datetime.now(timezone.utc)
    monkeypatch.setattr(
        upload_module,
        "validate_openrouter_keys",
        AsyncMock(return_value=response_validate_open_router_keys),
    )


async def _insert_quote(
    *,
    hotkey: str = FAKE_HOTKEY,
    amount_alpha_rao: int = FAKE_AMOUNT_ALPHA_RAO,
    created_at: datetime = FAKE_BLOCK_TIME - timedelta(minutes=1),
    expires_at: datetime = FAKE_BLOCK_TIME + timedelta(minutes=15),
) -> uuid.UUID:
    quote_id = uuid.uuid4()
    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO upload_payment_quotes (quote_id, miner_hotkey, amount_alpha_rao, created_at, expires_at)
            VALUES ($1, $2, $3, $4, $5)
            """,
            quote_id,
            hotkey,
            amount_alpha_rao,
            created_at,
            expires_at,
        )
    return quote_id


async def _call_post_agent(
    hotkey: str = FAKE_HOTKEY,
    name: str = "test-agent",
    quote_id: uuid.UUID | None = None,
    include_quote: bool = True,
) -> AgentUploadResponse:
    """Call the post agent endpoint with the given hotkey and name, using default mocks for all blockchain and S3 interactions.

    Parameters
    ----------
    hotkey : str, optional
        The hotkey of the miner, by default FAKE_HOTKEY
    name : str, optional
        The name of the agent, by default "test-agent"

    Returns
    -------
    AgentUploadResponse
        The response from the agent upload endpoint.
    """
    if quote_id is None and include_quote and hotkey != FAKE_OWNER_HOTKEY:
        quote_id = await _insert_quote(hotkey=hotkey)

    return await upload_module.post_agent(
        request=_make_request(),
        agent_file=_make_upload_file(),
        public_key="deadbeef",
        file_info=f"{hotkey}:0",
        signature="fakesig",
        name=name,
        payment_block_hash=FAKE_BLOCK_HASH,
        payment_extrinsic_index=FAKE_EXTRINSIC_INDEX,
        quote_id=str(quote_id) if quote_id is not None else None,
        openrouter_api_key="sk-or-v1-runtime",
        openrouter_management_key="sk-or-v1-management",
    )


async def _call_post_agent_as_owner() -> AgentUploadResponse:
    """Call post agent using the owner hotkey.

    Returns
    -------
    AgentUploadResponse
        The response from the agent upload endpoint.
    """
    return await _call_post_agent(
        hotkey=FAKE_OWNER_HOTKEY,
        name="owner-agent",
    )


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_check_agent_persists_payment_quote():
    """Preflight stores the server-side amount and destination for later payment validation."""
    response = await upload_module.check_agent_post(
        request=_make_request(),
        agent_file=_make_upload_file(),
        public_key="deadbeef",
        file_info=f"{FAKE_HOTKEY}:0",
        signature="fakesig",
        name="test-agent",
        openrouter_api_key="sk-or-v1-runtime",
        openrouter_management_key="sk-or-v1-management",
    )

    assert response.status == "success"
    assert response.amount_alpha_rao == FAKE_AMOUNT_ALPHA_RAO

    async with _db.pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT miner_hotkey, amount_alpha_rao, expires_at, created_at
            FROM upload_payment_quotes
            WHERE quote_id = $1
            """,
            response.quote_id,
        )
    assert row["miner_hotkey"] == FAKE_HOTKEY
    assert row["amount_alpha_rao"] == FAKE_AMOUNT_ALPHA_RAO
    assert row["expires_at"] > row["created_at"]


@pytest.mark.anyio
async def test_fresh_upload_creates_completed_payment():
    """Happy path: payment row is created and linked to the deterministic agent_id."""
    quote_id = await _insert_quote()
    response = await _call_post_agent(quote_id=quote_id)

    assert response.status == "success"
    payment = await retrieve_payment_by_hash(
        payment_block_hash=FAKE_BLOCK_HASH,
        payment_extrinsic_index=FAKE_EXTRINSIC_INDEX,
    )
    assert payment is not None
    assert payment.agent_id == _deterministic_id()
    assert payment.quote_id == quote_id


@pytest.mark.anyio
async def test_same_receipt_twice_raises_402():
    """A payment receipt already linked to an agent is rejected with 402."""
    from fastapi import HTTPException

    quote_id = await _insert_quote()
    await _call_post_agent(quote_id=quote_id)

    with pytest.raises(HTTPException) as exc_info:
        await _call_post_agent(quote_id=quote_id)

    assert exc_info.value.status_code == 402


@pytest.mark.anyio
async def test_partial_failure_retry_succeeds():
    """
    A prior attempt reserved the payment (agent_id=NULL) but crashed before
    creating the agent. The retry detects the incomplete row and finishes the upload.
    """
    quote_id = await _insert_quote()
    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO evaluation_payments
                (payment_block_hash, payment_extrinsic_index, agent_id, miner_hotkey, miner_coldkey, amount_alpha_rao, quote_id)
            VALUES ($1, $2, NULL, $3, $4, $5, $6)
            """,
            FAKE_BLOCK_HASH,
            FAKE_EXTRINSIC_INDEX,
            FAKE_HOTKEY,
            FAKE_COLDKEY,
            FAKE_AMOUNT_ALPHA_RAO,
            quote_id,
        )

    response = await _call_post_agent(quote_id=quote_id)

    assert response.status == "success"
    payment = await retrieve_payment_by_hash(
        payment_block_hash=FAKE_BLOCK_HASH,
        payment_extrinsic_index=FAKE_EXTRINSIC_INDEX,
    )
    assert payment.agent_id == _deterministic_id()


@pytest.mark.anyio
async def test_refunded_payment_raises_402():
    """A refunded payment is rejected before any reservation is attempted."""
    from fastapi import HTTPException

    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO failed_upload_refunds
                (id, block_hash, block_extrinsic_index, amount, tx_hash, upload_tx_hash, upload_block_hash, upload_block_extrinsic_index, coldkey, upload_amount)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
            uuid.uuid4(),
            "0xdeadbeef1235",
            "1",
            FAKE_AMOUNT_ALPHA_RAO,
            "0xrefundtxhash",
            "0xuploadtxhash",
            FAKE_BLOCK_HASH,
            FAKE_EXTRINSIC_INDEX,
            FAKE_COLDKEY,
            FAKE_AMOUNT_ALPHA_RAO,
        )

    with pytest.raises(HTTPException) as exc_info:
        await _call_post_agent()

    assert exc_info.value.status_code == 402
    payment = await retrieve_payment_by_hash(
        payment_block_hash=FAKE_BLOCK_HASH,
        payment_extrinsic_index=FAKE_EXTRINSIC_INDEX,
    )
    assert payment is None


@pytest.mark.anyio
async def test_burn_below_quote_raises_402(monkeypatch):
    """A burn event with an amount below the quoted amount is rejected before reservation."""
    from fastapi import HTTPException

    monkeypatch.setattr(
        upload_module.subtensor_client,
        "get_events",
        AsyncMock(return_value=_fake_events(1, FAKE_COLDKEY, upload_module.SN62_NETUID, FAKE_AMOUNT_ALPHA_RAO - 1)),
    )
    quote_id = await _insert_quote(amount_alpha_rao=FAKE_AMOUNT_ALPHA_RAO)

    with pytest.raises(HTTPException) as exc_info:
        await _call_post_agent(quote_id=quote_id)

    assert exc_info.value.status_code == 402
    payment = await retrieve_payment_by_hash(
        payment_block_hash=FAKE_BLOCK_HASH,
        payment_extrinsic_index=FAKE_EXTRINSIC_INDEX,
    )
    assert payment is None


@pytest.mark.anyio
async def test_burn_wrong_coldkey_raises_402(monkeypatch):
    """A burn event signed/attributed to a different coldkey than the miner's is rejected."""
    from fastapi import HTTPException

    monkeypatch.setattr(
        upload_module.subtensor_client,
        "get_events",
        AsyncMock(return_value=_fake_events(1, "5Fimposter", upload_module.SN62_NETUID, FAKE_AMOUNT_ALPHA_RAO)),
    )
    quote_id = await _insert_quote()

    with pytest.raises(HTTPException) as exc_info:
        await _call_post_agent(quote_id=quote_id)

    assert exc_info.value.status_code == 402


@pytest.mark.anyio
async def test_missing_quote_id_raises_clean_400():
    """Old clients are rejected with a clear error instead of stale pricing behavior."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await _call_post_agent(include_quote=False)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == upload_module.OUTDATED_UPLOAD_CLIENT_MESSAGE


@pytest.mark.anyio
async def test_quote_for_different_hotkey_raises_402():
    """A quote is bound to the miner hotkey that requested it."""
    from fastapi import HTTPException

    quote_id = await _insert_quote(hotkey="5OtherHotkey")

    with pytest.raises(HTTPException) as exc_info:
        await _call_post_agent(quote_id=quote_id)

    assert exc_info.value.status_code == 402


@pytest.mark.anyio
async def test_payment_outside_quote_window_raises_402():
    """The on-chain payment timestamp, not upload wall-clock time, must fit the quote window."""
    from fastapi import HTTPException

    quote_id = await _insert_quote(
        created_at=FAKE_BLOCK_TIME + timedelta(minutes=1),
        expires_at=FAKE_BLOCK_TIME + timedelta(minutes=15),
    )

    with pytest.raises(HTTPException) as exc_info:
        await _call_post_agent(quote_id=quote_id)

    assert exc_info.value.status_code == 402
    assert exc_info.value.detail == "Payment was made outside the quote validity window"


@pytest.mark.anyio
async def test_owner_bypasses_disallow_uploads():
    """Owner hotkey succeeds even when DISALLOW_UPLOADS is True; regular miner is blocked."""
    from fastapi import HTTPException

    original_flag = upload_module.config.DISALLOW_UPLOADS
    upload_module.config.DISALLOW_UPLOADS = True
    upload_module.config.DISALLOW_UPLOADS_REASON = "test freeze"
    try:
        response = await _call_post_agent_as_owner()
        assert response.status == "success"

        with pytest.raises(HTTPException) as exc_info:
            await _call_post_agent()
        assert exc_info.value.status_code == 503
    finally:
        upload_module.config.DISALLOW_UPLOADS = original_flag
        del upload_module.config.DISALLOW_UPLOADS_REASON


@pytest.mark.anyio
async def test_owner_bypasses_payment_creates_agent_without_payment_row():
    """Owner upload creates an agent record but writes no evaluation_payments row."""
    response = await _call_post_agent_as_owner()

    assert response.status == "success"

    payment = await retrieve_payment_by_hash(
        payment_block_hash="owner-placeholder-hash",
        payment_extrinsic_index="0",
    )
    assert payment is None

    async with _db.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT agent_id FROM agents WHERE miner_hotkey = $1",
            FAKE_OWNER_HOTKEY,
        )
    assert row is not None


@pytest.mark.anyio
async def test_owner_bypasses_rate_limit(monkeypatch):
    """Owner upload succeeds even when the rate-limit window has not expired."""
    from datetime import datetime, timezone

    from fastapi import HTTPException

    # Make the query return a just-now timestamp so check_rate_limit would fire
    monkeypatch.setattr(
        upload_module,
        "get_latest_agent_created_at_for_miner_hotkey_in_latest_set_id",
        AsyncMock(return_value=datetime.now(timezone.utc)),
    )
    # Make check_rate_limit always raise so we can confirm the owner bypass skips it
    monkeypatch.setattr(
        upload_module,
        "check_rate_limit",
        MagicMock(side_effect=HTTPException(status_code=429, detail="rate limited")),
    )

    response = await _call_post_agent_as_owner()
    assert response.status == "success"

    with pytest.raises(HTTPException) as exc_info:
        await _call_post_agent()
    assert exc_info.value.status_code == 429
