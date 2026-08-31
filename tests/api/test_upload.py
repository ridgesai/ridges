"""
Integration tests for the idempotent upload/payment flow.

All DB calls are real (Postgres via testcontainer). Blockchain and S3 are mocked.
One container starts per module; tables are truncated between tests.
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import utils.database as _db
from api.src.endpoints import upload as upload_module
from api.src.endpoints.upload import AgentUploadResponse
from models.agent import AgentCreate
from queries.agent import _derive_agent_id, create_agent
from queries.banned_coldkey import COLDKEY_BAN_LOCK_NAMESPACE, ban_coldkey
from queries.competition import initialize_current_competition_policy
from queries.errors import ColdkeyBannedError
from queries.payments import retrieve_payment_by_hash
from queries.upload_credit import (
    create_agent_with_upload_credit,
    credit_payment_identity,
    get_exact_upload_credit_replay,
)

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


def _fake_events(
    extrinsic_idx: int,
    coldkey: str,
    netuid: int,
    amount: int,
    hotkey: str = FAKE_HOTKEY,
) -> list:
    return [
        {
            "extrinsic_idx": extrinsic_idx,
            "event": {
                "module_id": "SubtensorModule",
                "event_id": "AlphaBurned",
                "attributes": (coldkey, hotkey, amount, netuid),
            },
        }
    ]


def _install_mocks(monkeypatch) -> None:
    """Patch blockchain + S3. prod flag is set by upload_prod_mode."""
    monkeypatch.setattr(upload_module, "check_signature", MagicMock())
    monkeypatch.setattr(upload_module, "check_hotkey_registered", AsyncMock())
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
        AsyncMock(
            return_value=_fake_events(
                1,
                FAKE_COLDKEY,
                upload_module.config.NETUID,
                FAKE_AMOUNT_ALPHA_RAO,
            )
        ),
    )
    monkeypatch.setattr(
        upload_module.subtensor_client,
        "get_hotkey_owner",
        AsyncMock(return_value=FAKE_COLDKEY),
    )
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
            return_value=MagicMock(
                amount_alpha_rao=FAKE_AMOUNT_ALPHA_RAO,
                payment_netuid=upload_module.config.NETUID,
            )
        ),
    )
    monkeypatch.setattr(upload_module, "upload_text_file_to_s3", AsyncMock())
    monkeypatch.setattr("queries.agent.upload_text_file_to_s3", AsyncMock())
    monkeypatch.setattr("queries.upload_credit.upload_text_file_to_s3", AsyncMock())
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
            INSERT INTO upload_payment_quotes
                (quote_id, miner_hotkey, amount_alpha_rao, created_at, expires_at)
            VALUES ($1, $2, $3, $4, $5)
            """,
            quote_id,
            hotkey,
            amount_alpha_rao,
            created_at,
            expires_at,
        )
    return quote_id


async def _insert_credit(
    *,
    hotkey: str = FAKE_HOTKEY,
    expires_at: datetime | None = None,
    revoked: bool = False,
) -> uuid.UUID:
    credit_id = uuid.uuid4()
    granted_at = expires_at - timedelta(hours=1) if expires_at is not None else datetime.now(timezone.utc)
    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO upload_credits (
                credit_id, miner_hotkey, reason, granted_by, granted_at, expires_at, revoked_at, revoked_by
            ) VALUES ($1, $2, 'test credit', 'pytest', $3, $4, $5, $6)
            """,
            credit_id,
            hotkey,
            granted_at,
            expires_at,
            datetime.now(timezone.utc) if revoked else None,
            "pytest" if revoked else None,
        )
    return credit_id


async def _insert_competition(
    set_id: int,
    *,
    name: str,
    start_date: datetime | None = None,
    submissions_closed_at: datetime | None = None,
    is_paused: bool = False,
    end_date: datetime | None = None,
    configured: bool = True,
) -> None:
    """Insert a competition using set 1's frozen policy when configured."""
    async with _db.pool.acquire() as conn:
        if configured:
            await conn.execute(
                """
                INSERT INTO competitions (
                    set_id, name, start_date, submissions_closed_at, is_paused,
                    emissions_end_at, end_date, raw_emission_weight,
                    scoring_mode, screener_1_threshold, screener_2_threshold,
                    prune_threshold, required_validator_count, pre_screening_enabled,
                    auto_approval_enabled, hardcoding_policy_version, incentive_enabled,
                    incentive_performance_threshold, incentive_cost_threshold,
                    incentive_reward_half_life_hours, incentive_time_multiplier_scale_hours
                )
                SELECT
                    $1, $2, $3, $4, $5,
                    CASE WHEN $4::timestamptz IS NULL THEN NULL ELSE $4::timestamptz END,
                    $6, 0,
                    scoring_mode, screener_1_threshold, screener_2_threshold,
                    prune_threshold, required_validator_count, pre_screening_enabled,
                    auto_approval_enabled, hardcoding_policy_version, incentive_enabled,
                    incentive_performance_threshold, incentive_cost_threshold,
                    incentive_reward_half_life_hours, incentive_time_multiplier_scale_hours
                FROM competitions
                WHERE set_id = 1
                """,
                set_id,
                name,
                start_date or datetime.now(timezone.utc),
                submissions_closed_at,
                is_paused,
                end_date,
            )
        else:
            await conn.execute(
                """
                INSERT INTO competitions (set_id, name, start_date, is_paused, end_date)
                VALUES ($1, $2, $3, $4, $5)
                """,
                set_id,
                name,
                start_date,
                is_paused,
                end_date,
            )


async def _call_post_agent(
    hotkey: str = FAKE_HOTKEY,
    name: str = "test-agent",
    quote_id: uuid.UUID | None = None,
    include_quote: bool = True,
    credit_id: uuid.UUID | None = None,
    content: bytes = b"async def agent_main(input): return 'ok'",
    set_id: int | None = 1,
    payment_block_hash: str = FAKE_BLOCK_HASH,
    payment_extrinsic_index: str = FAKE_EXTRINSIC_INDEX,
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
    if credit_id is None and quote_id is None and include_quote and hotkey != FAKE_OWNER_HOTKEY:
        quote_id = await _insert_quote(hotkey=hotkey)

    return await upload_module.post_agent(
        request=_make_request(),
        agent_file=_make_upload_file(content),
        public_key="deadbeef",
        file_info=f"{hotkey}:0",
        signature="fakesig",
        name=name,
        payment_block_hash=None if credit_id is not None else payment_block_hash,
        payment_extrinsic_index=None if credit_id is not None else payment_extrinsic_index,
        quote_id=str(quote_id) if quote_id is not None else None,
        credit_id=str(credit_id) if credit_id is not None else None,
        openrouter_api_key="sk-or-v1-runtime",
        openrouter_management_key="sk-or-v1-management",
        set_id=set_id,
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
        set_id=1,
    )

    assert response.status == "success"
    assert response.amount_alpha_rao == FAKE_AMOUNT_ALPHA_RAO
    assert response.payment_netuid == upload_module.config.NETUID
    upload_module.subtensor_client.get_alpha_stake_availability.assert_awaited_once_with(
        coldkey=FAKE_COLDKEY,
        hotkey=FAKE_HOTKEY,
        netuid=upload_module.config.NETUID,
    )

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
async def test_check_agent_with_credit_skips_rate_limit_and_burn_checks(monkeypatch):
    credit_id = await _insert_credit()
    monkeypatch.setattr(
        upload_module,
        "get_latest_agent_created_at_for_miner_hotkey_in_competition",
        AsyncMock(side_effect=AssertionError("credit preflight must not check the cooldown")),
    )

    response = await upload_module.check_agent_post(
        request=_make_request(),
        agent_file=_make_upload_file(),
        public_key="deadbeef",
        file_info=f"{FAKE_HOTKEY}:0",
        signature="fakesig",
        name="test-agent",
        openrouter_api_key="sk-or-v1-runtime",
        openrouter_management_key="sk-or-v1-management",
        use_credit=True,
        set_id=1,
    )

    assert response.payment_method == "credit"
    assert response.credit_id == credit_id
    assert response.amount_alpha_rao == 0
    assert response.quote_id is None
    upload_module.subtensor_client.get_alpha_stake_availability.assert_not_awaited()
    upload_module.get_upload_price.assert_not_awaited()


@pytest.mark.anyio
async def test_check_agent_dev_does_not_enforce_upload_cooldown(monkeypatch):
    monkeypatch.setattr(upload_module.config, "ENV", "dev")
    monkeypatch.setattr(
        upload_module,
        "get_latest_agent_created_at_for_miner_hotkey_in_competition",
        AsyncMock(side_effect=AssertionError("development preflight must not enforce production cooldown")),
    )

    response = await upload_module.check_agent_post(
        request=_make_request(),
        agent_file=_make_upload_file(),
        public_key="deadbeef",
        file_info=f"{FAKE_HOTKEY}:0",
        signature="fakesig",
        name="test-agent",
        openrouter_api_key="sk-or-v1-runtime",
        openrouter_management_key="sk-or-v1-management",
        set_id=1,
    )

    assert response.status == "success"
    assert response.set_id == 1


@pytest.mark.anyio
async def test_check_agent_with_credit_never_falls_back_to_burn():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await upload_module.check_agent_post(
            request=_make_request(),
            agent_file=_make_upload_file(),
            public_key="deadbeef",
            file_info=f"{FAKE_HOTKEY}:0",
            signature="fakesig",
            name="test-agent",
            openrouter_api_key="sk-or-v1-runtime",
            openrouter_management_key="sk-or-v1-management",
            use_credit=True,
            set_id=1,
        )

    assert exc_info.value.status_code == 402
    upload_module.subtensor_client.get_alpha_stake_availability.assert_not_awaited()
    upload_module.get_upload_price.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("unusable", ["expired", "revoked", "wrong_hotkey"])
async def test_check_agent_rejects_unusable_credit(unusable: str):
    from fastapi import HTTPException

    hotkey = "5FOtherHotkey" if unusable == "wrong_hotkey" else FAKE_HOTKEY
    expires_at = datetime.now(timezone.utc) - timedelta(minutes=1) if unusable == "expired" else None
    credit_id = await _insert_credit(hotkey=hotkey, expires_at=expires_at, revoked=unusable == "revoked")

    with pytest.raises(HTTPException) as exc_info:
        await upload_module.check_agent_post(
            request=_make_request(),
            agent_file=_make_upload_file(),
            public_key="deadbeef",
            file_info=f"{FAKE_HOTKEY}:0",
            signature="fakesig",
            name="test-agent",
            openrouter_api_key="sk-or-v1-runtime",
            openrouter_management_key="sk-or-v1-management",
            use_credit=True,
            credit_id=str(credit_id),
            set_id=1,
        )

    assert exc_info.value.status_code == 402


@pytest.mark.anyio
async def test_check_agent_rejects_banned_coldkey_before_stake_lookup():
    from fastapi import HTTPException

    await ban_coldkey(FAKE_COLDKEY, "test ban")

    with pytest.raises(HTTPException) as exc_info:
        await upload_module.check_agent_post(
            request=_make_request(),
            agent_file=_make_upload_file(),
            public_key="deadbeef",
            file_info=f"{FAKE_HOTKEY}:0",
            signature="fakesig",
            name="test-agent",
            openrouter_api_key="sk-or-v1-runtime",
            openrouter_management_key="sk-or-v1-management",
            set_id=1,
        )

    assert exc_info.value.status_code == 403
    upload_module.subtensor_client.get_alpha_stake_availability.assert_not_awaited()


@pytest.mark.anyio
async def test_check_agent_owner_bypasses_coldkey_ban(monkeypatch):
    monkeypatch.setattr(upload_module.config, "OWNER_HOTKEY", FAKE_HOTKEY)
    await ban_coldkey(FAKE_COLDKEY, "test ban")

    response = await upload_module.check_agent_post(
        request=_make_request(),
        agent_file=_make_upload_file(),
        public_key="deadbeef",
        file_info=f"{FAKE_HOTKEY}:0",
        signature="fakesig",
        name="owner-agent",
        openrouter_api_key="sk-or-v1-runtime",
        openrouter_management_key="sk-or-v1-management",
        set_id=1,
    )

    assert response.status == "success"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("position_rao", "total_rao", "locked_rao", "burnable_rao"),
    [
        (FAKE_AMOUNT_ALPHA_RAO - 1, FAKE_AMOUNT_ALPHA_RAO * 10, 0, FAKE_AMOUNT_ALPHA_RAO - 1),
        (
            FAKE_AMOUNT_ALPHA_RAO * 10,
            FAKE_AMOUNT_ALPHA_RAO * 10,
            FAKE_AMOUNT_ALPHA_RAO * 10 - FAKE_AMOUNT_ALPHA_RAO + 1,
            FAKE_AMOUNT_ALPHA_RAO - 1,
        ),
    ],
)
async def test_check_agent_rejects_position_or_lock_limited_alpha(
    monkeypatch,
    position_rao: int,
    total_rao: int,
    locked_rao: int,
    burnable_rao: int,
):
    from fastapi import HTTPException

    monkeypatch.setattr(
        upload_module.subtensor_client,
        "get_alpha_stake_availability",
        AsyncMock(
            return_value=SimpleNamespace(
                position_rao=position_rao,
                total_rao=total_rao,
                locked_rao=locked_rao,
                burnable_rao=burnable_rao,
            )
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        await upload_module.check_agent_post(
            request=_make_request(),
            agent_file=_make_upload_file(),
            public_key="deadbeef",
            file_info=f"{FAKE_HOTKEY}:0",
            signature="fakesig",
            name="test-agent",
            openrouter_api_key="sk-or-v1-runtime",
            openrouter_management_key="sk-or-v1-management",
            set_id=1,
        )

    assert exc_info.value.status_code == 402
    assert f"Position: {position_rao}" in exc_info.value.detail
    assert f"locked: {locked_rao}" in exc_info.value.detail
    assert f"burnable: {burnable_rao}" in exc_info.value.detail


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
    async with _db.pool.acquire() as conn:
        stored_coldkey = await conn.fetchval(
            "SELECT miner_coldkey FROM agents WHERE agent_id = $1",
            _deterministic_id(),
        )
    assert stored_coldkey == FAKE_COLDKEY


@pytest.mark.anyio
async def test_credit_upload_creates_agent_and_zero_value_payment():
    credit_id = await _insert_credit()

    response = await _call_post_agent(credit_id=credit_id)

    assert response.status == "success"
    payment_block_hash, payment_extrinsic_index = credit_payment_identity(credit_id)
    expected_agent_id = _derive_agent_id(payment_block_hash, payment_extrinsic_index)
    async with _db.pool.acquire() as conn:
        credit = await conn.fetchrow(
            "SELECT redeemed_at, redeemed_agent_id FROM upload_credits WHERE credit_id = $1",
            credit_id,
        )
        payment = await conn.fetchrow(
            """
            SELECT agent_id, miner_hotkey, miner_coldkey, amount_alpha_rao, quote_id, upload_credit_id
            FROM evaluation_payments
            WHERE payment_block_hash = $1 AND payment_extrinsic_index = $2
            """,
            payment_block_hash,
            payment_extrinsic_index,
        )

    assert credit["redeemed_at"] is not None
    assert credit["redeemed_agent_id"] == expected_agent_id
    assert payment["agent_id"] == expected_agent_id
    assert payment["miner_hotkey"] == FAKE_HOTKEY
    assert payment["miner_coldkey"] == FAKE_COLDKEY
    assert payment["amount_alpha_rao"] == 0
    assert payment["quote_id"] is None
    assert payment["upload_credit_id"] == credit_id
    upload_module.subtensor_client.get_block_info.assert_not_awaited()
    upload_module.subtensor_client.get_events.assert_not_awaited()


@pytest.mark.anyio
async def test_credit_upload_retry_is_idempotent():
    credit_id = await _insert_credit()

    first = await _call_post_agent(credit_id=credit_id)
    second = await _call_post_agent(credit_id=credit_id)
    payment_block_hash, payment_extrinsic_index = credit_payment_identity(credit_id)
    expected_agent_id = _derive_agent_id(payment_block_hash, payment_extrinsic_index)

    assert first.status == second.status == "success"
    assert "Successfully uploaded agent" in first.message
    assert str(credit_id) in second.message
    assert str(expected_agent_id) in second.message
    assert "was already used for agent" in second.message
    assert "No new agent was created" in second.message
    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM agents") == 1
        assert await conn.fetchval("SELECT count(*) FROM evaluation_payments") == 1


@pytest.mark.anyio
async def test_concurrent_credit_redemption_creates_one_agent():
    credit_id = await _insert_credit()
    payment_block_hash, payment_extrinsic_index = credit_payment_identity(credit_id)
    agent = AgentCreate(
        miner_hotkey=FAKE_HOTKEY,
        name="test-agent",
        version_num=0,
        created_at=datetime.now(timezone.utc),
        ip_address="127.0.0.1",
        payment_block_hash=payment_block_hash,
        payment_extrinsic_index=payment_extrinsic_index,
    )
    kwargs = {
        "credit_id": credit_id,
        "miner_hotkey": FAKE_HOTKEY,
        "miner_coldkey": FAKE_COLDKEY,
        "agent": agent,
        "agent_text": "async def agent_main(input): return 'ok'",
        "source_sha256": "concurrent-credit-source",
        "runtime_openrouter_api_key_ciphertext": b"runtime",
        "management_openrouter_api_key_ciphertext": b"management",
        "openrouter_workspace_id": "workspace",
        "openrouter_api_key_label": "label",
        "openrouter_api_key_creator_user_id": "creator",
        "openrouter_validated_at": datetime.now(timezone.utc),
        "set_id": 1,
    }

    first_result, second_result = await asyncio.gather(
        create_agent_with_upload_credit(**kwargs),
        create_agent_with_upload_credit(**kwargs),
    )

    first_agent_id, first_was_redeemed = first_result
    second_agent_id, second_was_redeemed = second_result
    assert first_agent_id == second_agent_id
    assert {first_was_redeemed, second_was_redeemed} == {False, True}
    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM agents") == 1
        assert await conn.fetchval("SELECT count(*) FROM evaluation_payments") == 1


@pytest.mark.anyio
async def test_redeemed_credit_rejects_different_source():
    from fastapi import HTTPException

    credit_id = await _insert_credit()
    await _call_post_agent(credit_id=credit_id)

    with pytest.raises(HTTPException) as exc_info:
        await _call_post_agent(credit_id=credit_id, content=b"async def agent_main(input): return 'changed'")

    assert exc_info.value.status_code == 409
    assert str(credit_id) in exc_info.value.detail
    assert "was already used for agent" in exc_info.value.detail
    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM agents") == 1
        assert await conn.fetchval("SELECT count(*) FROM evaluation_payments") == 1


@pytest.mark.anyio
async def test_final_credit_admission_enforces_cooldown_and_preserves_second_credit():
    from fastapi import HTTPException

    first_credit = await _insert_credit()
    second_credit = await _insert_credit()
    await _call_post_agent(credit_id=first_credit, content=b"print('first')")

    with pytest.raises(HTTPException) as exc_info:
        await _call_post_agent(credit_id=second_credit, content=b"print('second')")

    assert exc_info.value.status_code == 429
    async with _db.pool.acquire() as conn:
        second = await conn.fetchrow(
            "SELECT redeemed_at, redeemed_agent_id FROM upload_credits WHERE credit_id = $1",
            second_credit,
        )
        assert second["redeemed_at"] is None
        assert second["redeemed_agent_id"] is None
        assert await conn.fetchval("SELECT count(*) FROM agents") == 1
        assert await conn.fetchval("SELECT count(*) FROM evaluation_payments") == 1


@pytest.mark.anyio
async def test_final_burn_admission_enforces_cooldown_and_rolls_back_reservation():
    from fastapi import HTTPException

    await _call_post_agent(content=b"print('first')")
    second_block = "0xsecond-burn"

    with pytest.raises(HTTPException) as exc_info:
        await _call_post_agent(content=b"print('second')", payment_block_hash=second_block)

    assert exc_info.value.status_code == 429
    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM agents") == 1
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM evaluation_payments WHERE payment_block_hash = $1",
                second_block,
            )
            == 0
        )


@pytest.mark.anyio
async def test_final_cooldown_is_independent_across_competitions():
    await _insert_competition(2, name="Two")
    first = await _call_post_agent(content=b"print('first')", set_id=1)
    second = await _call_post_agent(
        content=b"print('second')",
        set_id=2,
        payment_block_hash="0xcross-set-burn",
    )

    assert first.status == second.status == "success"
    async with _db.pool.acquire() as conn:
        rows = await conn.fetch("SELECT set_id, version_num FROM agents ORDER BY set_id")
    assert [(row["set_id"], row["version_num"]) for row in rows] == [(1, 0), (2, 0)]


@pytest.mark.anyio
async def test_credit_redemption_rolls_back_when_agent_creation_fails(monkeypatch):
    credit_id = await _insert_credit()
    monkeypatch.setattr(upload_module, "upload_text_file_to_s3", AsyncMock(side_effect=RuntimeError("S3 failed")))

    with pytest.raises(RuntimeError, match="S3 failed"):
        await _call_post_agent(credit_id=credit_id)

    async with _db.pool.acquire() as conn:
        credit = await conn.fetchrow(
            "SELECT redeemed_at, redeemed_agent_id FROM upload_credits WHERE credit_id = $1",
            credit_id,
        )
        assert credit["redeemed_at"] is None
        assert credit["redeemed_agent_id"] is None
        assert await conn.fetchval("SELECT count(*) FROM agents") == 0
        assert await conn.fetchval("SELECT count(*) FROM evaluation_payments") == 0


@pytest.mark.anyio
async def test_credit_upload_rejects_burn_fields():
    from fastapi import HTTPException

    credit_id = await _insert_credit()

    with pytest.raises(HTTPException) as exc_info:
        await upload_module.post_agent(
            request=_make_request(),
            agent_file=_make_upload_file(),
            public_key="deadbeef",
            file_info=f"{FAKE_HOTKEY}:0",
            signature="fakesig",
            name="test-agent",
            payment_block_hash=FAKE_BLOCK_HASH,
            payment_extrinsic_index=FAKE_EXTRINSIC_INDEX,
            quote_id=str(await _insert_quote()),
            credit_id=str(credit_id),
            openrouter_api_key="sk-or-v1-runtime",
            openrouter_management_key="sk-or-v1-management",
        )

    assert exc_info.value.status_code == 400


@pytest.mark.anyio
async def test_final_upload_rejects_coldkey_banned_after_quote():
    from fastapi import HTTPException

    quote_id = await _insert_quote()
    await ban_coldkey(FAKE_COLDKEY, "banned after quote")

    with pytest.raises(HTTPException) as exc_info:
        await _call_post_agent(quote_id=quote_id)

    assert exc_info.value.status_code == 403
    upload_module.subtensor_client.get_events.assert_not_awaited()
    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM agents") == 0


@pytest.mark.anyio
async def test_agent_insert_rechecks_coldkey_ban_transactionally(monkeypatch):
    from fastapi import HTTPException

    quote_id = await _insert_quote()
    await ban_coldkey(FAKE_COLDKEY, "authoritative ban")
    monkeypatch.setattr(upload_module, "check_coldkey_banned", AsyncMock())

    with pytest.raises(HTTPException) as exc_info:
        await _call_post_agent(quote_id=quote_id)

    assert exc_info.value.status_code == 403
    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM agents") == 0


@pytest.mark.anyio
async def test_agent_insert_waits_for_concurrent_coldkey_ban():
    agent = AgentCreate(
        miner_hotkey=FAKE_HOTKEY,
        name="test-agent",
        version_num=0,
        created_at=datetime.now(timezone.utc),
        ip_address="127.0.0.1",
        payment_block_hash="concurrent-ban-block",
        payment_extrinsic_index="1",
    )

    async with _db.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock($1, hashtext($2))",
                COLDKEY_BAN_LOCK_NAMESPACE,
                FAKE_COLDKEY,
            )
            create_task = asyncio.create_task(
                create_agent(
                    agent,
                    "print('test')",
                    source_sha256="concurrent-ban-source",
                    runtime_openrouter_api_key_ciphertext=b"runtime",
                    management_openrouter_api_key_ciphertext=b"management",
                    openrouter_workspace_id="workspace",
                    openrouter_api_key_label="label",
                    openrouter_api_key_creator_user_id="creator",
                    openrouter_validated_at=datetime.now(timezone.utc),
                    miner_coldkey=FAKE_COLDKEY,
                )
            )
            await asyncio.sleep(0.05)
            assert not create_task.done()
            await conn.execute(
                "INSERT INTO banned_coldkeys (miner_coldkey, banned_reason) VALUES ($1, $2)",
                FAKE_COLDKEY,
                "concurrent ban",
            )

    with pytest.raises(ColdkeyBannedError):
        await asyncio.wait_for(create_task, timeout=2)

    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM agents") == 0


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
        AsyncMock(
            return_value=_fake_events(
                1,
                FAKE_COLDKEY,
                upload_module.config.NETUID,
                FAKE_AMOUNT_ALPHA_RAO - 1,
            )
        ),
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
        AsyncMock(
            return_value=_fake_events(
                1,
                "5Fimposter",
                upload_module.config.NETUID,
                FAKE_AMOUNT_ALPHA_RAO,
            )
        ),
    )
    quote_id = await _insert_quote()

    with pytest.raises(HTTPException) as exc_info:
        await _call_post_agent(quote_id=quote_id)

    assert exc_info.value.status_code == 402


@pytest.mark.anyio
async def test_burn_wrong_hotkey_raises_402(monkeypatch):
    """A burn from another stake position cannot pay for this miner hotkey's upload."""
    from fastapi import HTTPException

    monkeypatch.setattr(
        upload_module.subtensor_client,
        "get_events",
        AsyncMock(
            return_value=_fake_events(
                1,
                FAKE_COLDKEY,
                upload_module.config.NETUID,
                FAKE_AMOUNT_ALPHA_RAO,
                hotkey="5FOtherHotkey",
            )
        ),
    )
    quote_id = await _insert_quote()

    with pytest.raises(HTTPException) as exc_info:
        await _call_post_agent(quote_id=quote_id)

    assert exc_info.value.status_code == 402
    assert exc_info.value.detail == "Hotkey does not match"


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
async def test_burn_on_different_subnet_raises_402(monkeypatch):
    """The AlphaBurned event must match the subnet persisted on the quote."""
    from fastapi import HTTPException

    monkeypatch.setattr(
        upload_module.subtensor_client,
        "get_events",
        AsyncMock(return_value=_fake_events(1, FAKE_COLDKEY, 63, FAKE_AMOUNT_ALPHA_RAO)),
    )
    quote_id = await _insert_quote()

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
            "SELECT agent_id, miner_coldkey FROM agents WHERE miner_hotkey = $1",
            FAKE_OWNER_HOTKEY,
        )
    assert row is not None
    assert row["miner_coldkey"] is None


@pytest.mark.anyio
async def test_owner_bypasses_rate_limit():
    """Owner upload succeeds even when the rate-limit window has not expired."""
    response = await _call_post_agent_as_owner()
    assert response.status == "success"


@pytest.mark.anyio
async def test_preflight_requires_explicit_competition_and_honors_the_choice():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await upload_module.check_agent_post(
            request=_make_request(),
            agent_file=_make_upload_file(),
            public_key="deadbeef",
            file_info=f"{FAKE_HOTKEY}:0",
            signature="fakesig",
            name="test-agent",
            openrouter_api_key="sk-or-v1-runtime",
            openrouter_management_key="sk-or-v1-management",
        )
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == upload_module.OUTDATED_UPLOAD_CLIENT_MESSAGE

    await _insert_competition(2, name="Two")
    for chosen_set_id in (1, 2):
        explicit = await upload_module.check_agent_post(
            request=_make_request(),
            agent_file=_make_upload_file(),
            public_key="deadbeef",
            file_info=f"{FAKE_HOTKEY}:0",
            signature="fakesig",
            name="test-agent",
            openrouter_api_key="sk-or-v1-runtime",
            openrouter_management_key="sk-or-v1-management",
            set_id=chosen_set_id,
        )
        assert explicit.set_id == chosen_set_id


@pytest.mark.anyio
async def test_preflight_rejects_non_accepting_competition():
    from fastapi import HTTPException

    async with _db.pool.acquire() as conn:
        await conn.execute("UPDATE competitions SET is_paused = TRUE WHERE set_id = 1")

    with pytest.raises(HTTPException) as exc_info:
        await upload_module.check_agent_post(
            request=_make_request(),
            agent_file=_make_upload_file(),
            public_key="deadbeef",
            file_info=f"{FAKE_HOTKEY}:0",
            signature="fakesig",
            name="test-agent",
            openrouter_api_key="sk-or-v1-runtime",
            openrouter_management_key="sk-or-v1-management",
            set_id=1,
        )
    assert exc_info.value.status_code == 409


@pytest.mark.anyio
async def test_preflight_selection_is_pinned_when_another_competition_opens():
    preflight = await upload_module.check_agent_post(
        request=_make_request(),
        agent_file=_make_upload_file(),
        public_key="deadbeef",
        file_info=f"{FAKE_HOTKEY}:0",
        signature="fakesig",
        name="test-agent",
        openrouter_api_key="sk-or-v1-runtime",
        openrouter_management_key="sk-or-v1-management",
        set_id=1,
    )
    assert preflight.set_id == 1
    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE upload_payment_quotes
            SET created_at = $2, expires_at = $3
            WHERE quote_id = $1
            """,
            preflight.quote_id,
            FAKE_BLOCK_TIME - timedelta(minutes=1),
            FAKE_BLOCK_TIME + timedelta(minutes=15),
        )
    await _insert_competition(2, name="Two")

    response = await _call_post_agent(quote_id=preflight.quote_id, set_id=preflight.set_id)

    assert response.status == "success"
    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT set_id FROM agents") == 1


@pytest.mark.anyio
async def test_explicit_final_never_falls_back_when_selected_competition_closes():
    from fastapi import HTTPException

    await _insert_competition(2, name="Two")
    quote_id = await _insert_quote()
    async with _db.pool.acquire() as conn:
        await conn.execute("UPDATE competitions SET is_paused = TRUE WHERE set_id = 1")

    with pytest.raises(HTTPException) as exc_info:
        await _call_post_agent(quote_id=quote_id, set_id=1)

    assert exc_info.value.status_code == 409
    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM agents") == 0
        assert await conn.fetchval("SELECT count(*) FROM evaluation_payments") == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    "lifecycle_update",
    [
        "UPDATE competitions SET is_paused = TRUE WHERE set_id = 1",
        """
        UPDATE competitions
        SET submissions_closed_at = clock_timestamp(), emissions_end_at = clock_timestamp()
        WHERE set_id = 1
        """,
        "UPDATE competitions SET end_date = clock_timestamp() WHERE set_id = 1",
    ],
    ids=["paused", "submissions-closed", "ended"],
)
async def test_exact_credit_replay_survives_lifecycle_change_and_rejects_conflicts(lifecycle_update: str):
    from fastapi import HTTPException

    credit_id = await _insert_credit()
    first = await _call_post_agent(credit_id=credit_id, set_id=1)
    assert first.status == "success"
    upload_module.upload_text_file_to_s3.reset_mock()
    async with _db.pool.acquire() as conn:
        await conn.execute(lifecycle_update)

    replay = await _call_post_agent(credit_id=credit_id)
    assert replay.status == "success"
    assert "No new agent was created" in replay.message
    upload_module.upload_text_file_to_s3.assert_not_awaited()

    with pytest.raises(HTTPException) as source_conflict:
        await _call_post_agent(
            credit_id=credit_id,
            content=b"async def agent_main(input): return 'changed'",
        )
    assert source_conflict.value.status_code == 409

    with pytest.raises(HTTPException) as set_conflict:
        await _call_post_agent(credit_id=credit_id, set_id=2)
    assert set_conflict.value.status_code == 409

    with pytest.raises(HTTPException) as hotkey_conflict:
        await _call_post_agent(credit_id=credit_id, hotkey="different-hotkey")
    assert hotkey_conflict.value.status_code == 409


@pytest.mark.anyio
async def test_credit_retry_rechecks_exact_replay_after_selection_loses_to_close(monkeypatch):
    credit_id = await _insert_credit()
    initial_read_complete = asyncio.Event()
    release_initial_read = asyncio.Event()
    retry_task = None
    held_initial_read = False

    async def gated_replay_check(**kwargs):
        nonlocal held_initial_read
        result = await get_exact_upload_credit_replay(**kwargs)
        if asyncio.current_task() is retry_task and not held_initial_read:
            held_initial_read = True
            assert result is None
            initial_read_complete.set()
            await release_initial_read.wait()
        return result

    monkeypatch.setattr(upload_module, "get_exact_upload_credit_replay", gated_replay_check)
    retry_task = asyncio.create_task(_call_post_agent(credit_id=credit_id, set_id=1), name="credit-retry")
    await asyncio.wait_for(initial_read_complete.wait(), timeout=2)

    admitted = await _call_post_agent(credit_id=credit_id, set_id=1)
    async with _db.pool.acquire() as conn:
        await conn.execute("UPDATE competitions SET is_paused = TRUE WHERE set_id = 1")
    release_initial_read.set()
    replayed = await asyncio.wait_for(retry_task, timeout=2)

    assert admitted.status == replayed.status == "success"
    assert "No new agent was created" in replayed.message
    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM agents") == 1
        assert await conn.fetchval("SELECT count(*) FROM evaluation_payments") == 1


@pytest.mark.anyio
async def test_credit_retry_rechecks_exact_replay_after_admission_loses_to_close(monkeypatch):
    credit_id = await _insert_credit()
    reached_s3 = asyncio.Event()
    release_s3 = asyncio.Event()
    retry_task = None
    held_retry_s3 = False
    mocked_s3 = upload_module.upload_text_file_to_s3

    async def gated_s3(*args, **kwargs):
        nonlocal held_retry_s3
        if asyncio.current_task() is retry_task and not held_retry_s3:
            held_retry_s3 = True
            reached_s3.set()
            await release_s3.wait()
        return await mocked_s3(*args, **kwargs)

    monkeypatch.setattr(upload_module, "upload_text_file_to_s3", gated_s3)
    retry_task = asyncio.create_task(_call_post_agent(credit_id=credit_id, set_id=1), name="credit-retry")
    await asyncio.wait_for(reached_s3.wait(), timeout=2)

    admitted = await _call_post_agent(credit_id=credit_id, set_id=1)
    async with _db.pool.acquire() as conn:
        await conn.execute("UPDATE competitions SET is_paused = TRUE WHERE set_id = 1")
    release_s3.set()
    replayed = await asyncio.wait_for(retry_task, timeout=2)

    assert admitted.status == replayed.status == "success"
    assert "No new agent was created" in replayed.message
    assert mocked_s3.await_count == 2
    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM agents") == 1
        assert await conn.fetchval("SELECT count(*) FROM evaluation_payments") == 1


@pytest.mark.anyio
async def test_unredeemed_credit_preserves_lifecycle_conflict():
    from fastapi import HTTPException

    credit_id = await _insert_credit()
    async with _db.pool.acquire() as conn:
        await conn.execute("UPDATE competitions SET is_paused = TRUE WHERE set_id = 1")

    with pytest.raises(HTTPException) as exc_info:
        await _call_post_agent(credit_id=credit_id, set_id=1)

    assert exc_info.value.status_code == 409
    async with _db.pool.acquire() as conn:
        credit = await conn.fetchrow(
            "SELECT redeemed_at, redeemed_agent_id FROM upload_credits WHERE credit_id = $1",
            credit_id,
        )
        assert credit["redeemed_at"] is None
        assert credit["redeemed_agent_id"] is None


@pytest.mark.anyio
async def test_burn_s3_failure_does_not_reserve_or_consume_funding(monkeypatch):
    quote_id = await _insert_quote()
    monkeypatch.setattr(upload_module, "upload_text_file_to_s3", AsyncMock(side_effect=RuntimeError("S3 failed")))

    with pytest.raises(RuntimeError, match="S3 failed"):
        await _call_post_agent(quote_id=quote_id)

    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM agents") == 0
        assert await conn.fetchval("SELECT count(*) FROM evaluation_payments") == 0


@pytest.mark.anyio
async def test_database_failure_after_s3_rolls_back_agent_and_burn(monkeypatch):
    quote_id = await _insert_quote()
    async with _db.pool.acquire() as conn:
        await conn.execute("UPDATE competitions SET pre_screening_enabled = TRUE WHERE set_id = 1")
    insert_job = AsyncMock(side_effect=RuntimeError("job insert failed"))
    monkeypatch.setattr("queries.pre_screening_judge.insert_pending_pre_screening_job", insert_job)

    with pytest.raises(RuntimeError, match="job insert failed"):
        await _call_post_agent(quote_id=quote_id)

    upload_module.upload_text_file_to_s3.assert_awaited_once()
    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM agents") == 0
        assert await conn.fetchval("SELECT count(*) FROM evaluation_payments") == 0
        assert await conn.fetchval("SELECT count(*) FROM screener_1_queue") == 0


@pytest.mark.anyio
async def test_openapi_exposes_only_the_frozen_upload_competition_contract():
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(upload_module.router, prefix="/upload")
    schema = app.openapi()

    def properties_for(path: str, media_type: str) -> dict:
        body_schema = schema["paths"][path]["post"]["requestBody"]["content"][media_type]["schema"]
        component = body_schema["$ref"].rsplit("/", 1)[-1]
        return schema["components"]["schemas"][component]["properties"]

    for path in ("/upload/agent/check", "/upload/agent", "/upload/agent/ticket"):
        assert "set_id" in properties_for(path, "multipart/form-data")

    assert "set_id" not in properties_for("/upload/prepare", "application/json")
    assert "set_id" not in properties_for("/upload/ticket/check", "application/json")

    response_ref = schema["paths"]["/upload/agent/check"]["post"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]["$ref"]
    response_properties = schema["components"]["schemas"][response_ref.rsplit("/", 1)[-1]]["properties"]
    assert "set_id" in response_properties
    response_required = schema["components"]["schemas"][response_ref.rsplit("/", 1)[-1]]["required"]
    assert "set_id" in response_required

    prepare_response_ref = schema["paths"]["/upload/prepare"]["post"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    prepare_response_properties = schema["components"]["schemas"][prepare_response_ref.rsplit("/", 1)[-1]]["properties"]
    assert "set_id" not in prepare_response_properties

    assert "/upload/competitions" not in schema["paths"]
