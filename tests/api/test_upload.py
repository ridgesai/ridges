"""
Integration tests for the idempotent upload/payment flow.

All DB calls are real (Postgres via testcontainer). Blockchain and S3 are mocked.
One container starts per module; tables are truncated between tests.
"""

import uuid
from datetime import datetime, timezone
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
FAKE_AMOUNT_RAO = 100_000_000
FAKE_SEND_ADDRESS = "5FUploadWalletAddress"

# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module", autouse=True)
def upload_prod_mode():
    """Run all tests in this module against the prod code path."""
    original_prod = upload_module.prod
    original_send_address = upload_module.config.UPLOAD_SEND_ADDRESS
    upload_module.prod = True
    upload_module.config.UPLOAD_SEND_ADDRESS = FAKE_SEND_ADDRESS
    yield
    upload_module.prod = original_prod
    upload_module.config.UPLOAD_SEND_ADDRESS = original_send_address


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db):
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE evaluation_payments, agents, failed_upload_refunds, upload_attempts RESTART IDENTITY CASCADE"
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


def _make_fake_extrinsic(coldkey: str, amount_rao: int, dest: str) -> MagicMock:
    ext = MagicMock()
    ext.value = {
        "call": {
            "call_args": [
                {"name": "dest", "value": dest},
                {"name": "value", "value": amount_rao},
            ]
        }
    }
    ext.__getitem__ = MagicMock(side_effect=lambda key: coldkey if key == "address" else None)
    return ext


def _install_mocks(monkeypatch) -> None:
    """Patch blockchain + S3. prod flag and UPLOAD_SEND_ADDRESS are set by upload_prod_mode."""
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
        "get_block",
        AsyncMock(
            return_value={
                "header": {"number": 42},
                "extrinsics": [
                    MagicMock(),
                    _make_fake_extrinsic(FAKE_COLDKEY, FAKE_AMOUNT_RAO, FAKE_SEND_ADDRESS),
                ],
            }
        ),
    )
    monkeypatch.setattr(
        upload_module.subtensor_client,
        "get_hotkey_owner",
        AsyncMock(return_value=FAKE_COLDKEY),
    )
    monkeypatch.setattr(
        upload_module,
        "get_upload_price",
        AsyncMock(return_value=MagicMock(amount_rao=FAKE_AMOUNT_RAO)),
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


async def _call_post_agent() -> AgentUploadResponse:
    return await upload_module.post_agent(
        request=_make_request(),
        agent_file=_make_upload_file(),
        public_key="deadbeef",
        file_info=f"{FAKE_HOTKEY}:0",
        signature="fakesig",
        name="test-agent",
        payment_block_hash=FAKE_BLOCK_HASH,
        payment_extrinsic_index=FAKE_EXTRINSIC_INDEX,
        payment_time=0.0,
    )


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_fresh_upload_creates_completed_payment():
    """Happy path: payment row is created and linked to the deterministic agent_id."""
    response = await _call_post_agent()

    assert response.status == "success"
    payment = await retrieve_payment_by_hash(
        payment_block_hash=FAKE_BLOCK_HASH,
        payment_extrinsic_index=FAKE_EXTRINSIC_INDEX,
    )
    assert payment is not None
    assert payment.agent_id == _deterministic_id()


@pytest.mark.anyio
async def test_same_receipt_twice_raises_402():
    """A payment receipt already linked to an agent is rejected with 402."""
    from fastapi import HTTPException

    await _call_post_agent()

    with pytest.raises(HTTPException) as exc_info:
        await _call_post_agent()

    assert exc_info.value.status_code == 402


@pytest.mark.anyio
async def test_partial_failure_retry_succeeds():
    """
    A prior attempt reserved the payment (agent_id=NULL) but crashed before
    creating the agent. The retry detects the incomplete row and finishes the upload.
    """
    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO evaluation_payments
                (payment_block_hash, payment_extrinsic_index, agent_id, miner_hotkey, miner_coldkey, amount_rao)
            VALUES ($1, $2, NULL, $3, $4, $5)
            """,
            FAKE_BLOCK_HASH,
            FAKE_EXTRINSIC_INDEX,
            FAKE_HOTKEY,
            FAKE_COLDKEY,
            FAKE_AMOUNT_RAO,
        )

    response = await _call_post_agent()

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
            FAKE_AMOUNT_RAO,
            "0xrefundtxhash",
            "0xuploadtxhash",
            FAKE_BLOCK_HASH,
            FAKE_EXTRINSIC_INDEX,
            FAKE_COLDKEY,
            FAKE_AMOUNT_RAO,
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
async def test_amount_mismatch_raises_402(monkeypatch):
    """A payment with the wrong on-chain amount is rejected before reservation."""
    from fastapi import HTTPException

    monkeypatch.setattr(
        upload_module,
        "get_upload_price",
        AsyncMock(return_value=MagicMock(amount_rao=FAKE_AMOUNT_RAO + 1)),
    )

    with pytest.raises(HTTPException) as exc_info:
        await _call_post_agent()

    assert exc_info.value.status_code == 402
    payment = await retrieve_payment_by_hash(
        payment_block_hash=FAKE_BLOCK_HASH,
        payment_extrinsic_index=FAKE_EXTRINSIC_INDEX,
    )
    assert payment is None
