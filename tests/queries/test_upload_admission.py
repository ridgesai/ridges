from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

import utils.database as _db
from models.agent import AgentCreate
from models.competition import CompetitionPolicy
from queries.agent import BurnUploadFunding, CreditUploadFunding, admit_agent
from queries.errors import (
    ColdkeyBannedError,
    CompetitionNotAcceptingSubmissionsError,
    DuplicateAgentIDError,
    UploadCooldownError,
)
from utils.database import db_operation

pytestmark = pytest.mark.anyio


def _policy(*, pre_screening_enabled: bool = False) -> CompetitionPolicy:
    return CompetitionPolicy(
        scoring_mode="consensus",
        screener_1_threshold=0.4,
        screener_2_threshold=0.4,
        prune_threshold=0.4,
        required_validator_count=2,
        pre_screening_enabled=pre_screening_enabled,
        auto_approval_enabled=False,
        hardcoding_policy_version="hardcoding-v1",
        incentive_enabled=False,
        incentive_performance_threshold=0.03,
        incentive_cost_threshold=0.06,
        incentive_reward_half_life_hours=336.0,
        incentive_time_multiplier_scale_hours=12.0,
    )


async def _insert_competition(set_id: int, *, pre_screening_enabled: bool = False) -> None:
    values = _policy(pre_screening_enabled=pre_screening_enabled).model_dump()
    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO competitions (
                set_id, name, start_date, scoring_mode, screener_1_threshold,
                screener_2_threshold, prune_threshold, required_validator_count,
                pre_screening_enabled, auto_approval_enabled,
                hardcoding_policy_version, incentive_enabled,
                incentive_performance_threshold, incentive_cost_threshold,
                incentive_reward_half_life_hours, incentive_time_multiplier_scale_hours
            ) VALUES (
                $1, $2, clock_timestamp() - INTERVAL '1 day', $3, $4, $5, $6,
                $7, $8, $9, $10, $11, $12, $13, $14, $15
            )
            """,
            set_id,
            f"Competition {set_id}",
            values["scoring_mode"],
            values["screener_1_threshold"],
            values["screener_2_threshold"],
            values["prune_threshold"],
            values["required_validator_count"],
            values["pre_screening_enabled"],
            values["auto_approval_enabled"],
            values["hardcoding_policy_version"],
            values["incentive_enabled"],
            values["incentive_performance_threshold"],
            values["incentive_cost_threshold"],
            values["incentive_reward_half_life_hours"],
            values["incentive_time_multiplier_scale_hours"],
        )


async def _insert_credit(hotkey: str) -> CreditUploadFunding:
    credit_id = uuid4()
    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO upload_credits (credit_id, miner_hotkey, reason, granted_by)
            VALUES ($1, $2, 'test', 'pytest')
            """,
            credit_id,
            hotkey,
        )
    return CreditUploadFunding(credit_id=credit_id, miner_hotkey=hotkey, miner_coldkey=f"cold-{hotkey}")


async def _insert_burn(hotkey: str, identity: str) -> BurnUploadFunding:
    quote_id = uuid4()
    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO upload_payment_quotes (
                quote_id, miner_hotkey, amount_alpha_rao, created_at, expires_at
            ) VALUES ($1, $2, 100, clock_timestamp(), clock_timestamp() + INTERVAL '1 hour')
            """,
            quote_id,
            hotkey,
        )
    return BurnUploadFunding(
        payment_block_hash=f"block-{identity}",
        payment_extrinsic_index="0",
        miner_hotkey=hotkey,
        miner_coldkey=f"cold-{hotkey}",
        amount_alpha_rao=100,
        quote_id=quote_id,
    )


def _agent(
    *,
    hotkey: str,
    name: str,
    identity: str,
    funding: BurnUploadFunding | CreditUploadFunding | None = None,
) -> AgentCreate:
    if isinstance(funding, BurnUploadFunding):
        block_hash = funding.payment_block_hash
        extrinsic_index = funding.payment_extrinsic_index
    elif isinstance(funding, CreditUploadFunding):
        block_hash = f"credit:{funding.credit_id}"
        extrinsic_index = "0"
    else:
        block_hash = f"unfunded-{identity}"
        extrinsic_index = "0"
    return AgentCreate(
        miner_hotkey=hotkey,
        name=name,
        version_num=999,
        created_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
        ip_address="127.0.0.1",
        payment_block_hash=block_hash,
        payment_extrinsic_index=extrinsic_index,
    )


async def _admit(
    *,
    set_id: int,
    hotkey: str,
    name: str,
    identity: str,
    source: str,
    funding: BurnUploadFunding | CreditUploadFunding | None = None,
    enforce_cooldown: bool = False,
):
    return await admit_agent(
        _agent(hotkey=hotkey, name=name, identity=identity, funding=funding),
        set_id=set_id,
        source_sha256=source,
        runtime_openrouter_api_key_ciphertext=b"runtime",
        management_openrouter_api_key_ciphertext=b"management",
        openrouter_workspace_id="workspace",
        openrouter_api_key_label="label",
        openrouter_api_key_creator_user_id="creator",
        openrouter_validated_at=datetime.now(timezone.utc),
        miner_coldkey=None if funding is None else funding.miner_coldkey,
        funding=funding,
        enforce_cooldown=enforce_cooldown,
    )


async def _funding(kind: str, hotkey: str, identity: str):
    if kind == "burn":
        return await _insert_burn(hotkey, identity)
    return await _insert_credit(hotkey)


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db):
    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            TRUNCATE competitions, upload_payment_quotes, upload_credits, banned_coldkeys
            RESTART IDENTITY CASCADE
            """
        )
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            TRUNCATE competitions, upload_payment_quotes, upload_credits, banned_coldkeys
            RESTART IDENTITY CASCADE
            """
        )


async def test_selected_set_controls_name_version_cooldown_and_clock_time() -> None:
    await _insert_competition(1)
    await _insert_competition(2)

    first = await _admit(set_id=1, hotkey="hotkey", name="Alpha", identity="one", source="source-one")
    second = await _admit(set_id=1, hotkey="hotkey", name="Ignored", identity="two", source="source-two")
    cross_set = await _admit(set_id=2, hotkey="hotkey", name="Beta", identity="three", source="source-three")

    async with _db.pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT agent_id, set_id, name, version_num, created_at
            FROM agents
            ORDER BY set_id, version_num
            """
        )
    assert [(row["set_id"], row["name"], row["version_num"]) for row in rows] == [
        (1, "Alpha", 0),
        (1, "Alpha", 1),
        (2, "Beta", 0),
    ]
    assert {row["agent_id"] for row in rows} == {first.agent_id, second.agent_id, cross_set.agent_id}
    assert all(row["created_at"].year != 2000 for row in rows)

    with pytest.raises(UploadCooldownError):
        await _admit(
            set_id=1,
            hotkey="hotkey",
            name="Ignored",
            identity="four",
            source="source-four",
            enforce_cooldown=True,
        )


async def test_cooldown_is_independent_across_competitions() -> None:
    await _insert_competition(1)
    await _insert_competition(2)
    await _admit(set_id=1, hotkey="hotkey", name="Alpha", identity="one", source="source-one")

    result = await _admit(
        set_id=2,
        hotkey="hotkey",
        name="Beta",
        identity="two",
        source="source-two",
        enforce_cooldown=True,
    )

    async with _db.pool.acquire() as conn:
        row = await conn.fetchrow("SELECT set_id, name, version_num FROM agents WHERE agent_id = $1", result.agent_id)
    assert dict(row) == {"set_id": 2, "name": "Beta", "version_num": 0}


async def test_concurrent_same_hotkey_same_set_serializes_name_and_versions() -> None:
    await _insert_competition(1)
    first_funding = await _insert_credit("hotkey")
    second_funding = await _insert_credit("hotkey")

    first, second = await asyncio.gather(
        _admit(
            set_id=1,
            hotkey="hotkey",
            name="First",
            identity="one",
            source="source-one",
            funding=first_funding,
        ),
        _admit(
            set_id=1,
            hotkey="hotkey",
            name="Second",
            identity="two",
            source="source-two",
            funding=second_funding,
        ),
    )

    async with _db.pool.acquire() as conn:
        rows = await conn.fetch("SELECT agent_id, name, version_num FROM agents ORDER BY version_num")
        redeemed = await conn.fetchval("SELECT count(*) FROM upload_credits WHERE redeemed_at IS NOT NULL")
    assert {row["agent_id"] for row in rows} == {first.agent_id, second.agent_id}
    assert [row["version_num"] for row in rows] == [0, 1]
    assert len({row["name"] for row in rows}) == 1
    assert rows[0]["name"] in {"First", "Second"}
    assert redeemed == 2


async def test_concurrent_same_hotkey_cooldown_rolls_back_loser_funding() -> None:
    await _insert_competition(1)
    first_funding = await _insert_credit("hotkey")
    second_funding = await _insert_credit("hotkey")

    outcomes = await asyncio.gather(
        _admit(
            set_id=1,
            hotkey="hotkey",
            name="First",
            identity="one",
            source="source-one",
            funding=first_funding,
            enforce_cooldown=True,
        ),
        _admit(
            set_id=1,
            hotkey="hotkey",
            name="Second",
            identity="two",
            source="source-two",
            funding=second_funding,
            enforce_cooldown=True,
        ),
        return_exceptions=True,
    )

    assert sum(not isinstance(outcome, Exception) for outcome in outcomes) == 1
    assert sum(isinstance(outcome, UploadCooldownError) for outcome in outcomes) == 1
    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM agents") == 1
        assert await conn.fetchval("SELECT count(*) FROM evaluation_payments") == 1
        assert await conn.fetchval("SELECT count(*) FROM upload_credits WHERE redeemed_at IS NOT NULL") == 1
        assert await conn.fetchval("SELECT count(*) FROM upload_credits WHERE redeemed_at IS NULL") == 1


async def test_concurrent_same_hotkey_different_sets_has_independent_lanes() -> None:
    await _insert_competition(1)
    await _insert_competition(2)
    first_funding = await _insert_credit("hotkey")
    second_funding = await _insert_credit("hotkey")

    first, second = await asyncio.gather(
        _admit(
            set_id=1,
            hotkey="hotkey",
            name="First",
            identity="one",
            source="source-one",
            funding=first_funding,
            enforce_cooldown=True,
        ),
        _admit(
            set_id=2,
            hotkey="hotkey",
            name="Second",
            identity="two",
            source="source-two",
            funding=second_funding,
            enforce_cooldown=True,
        ),
    )

    async with _db.pool.acquire() as conn:
        rows = await conn.fetch("SELECT agent_id, set_id, name, version_num FROM agents ORDER BY set_id")
    assert {row["agent_id"] for row in rows} == {first.agent_id, second.agent_id}
    assert [(row["set_id"], row["name"], row["version_num"]) for row in rows] == [
        (1, "First", 0),
        (2, "Second", 0),
    ]


async def test_concurrent_same_source_is_funded_and_terminal_only_within_set() -> None:
    await _insert_competition(1, pre_screening_enabled=True)
    await _insert_competition(2, pre_screening_enabled=True)
    first_funding = await _insert_credit("hotkey-one")
    second_funding = await _insert_credit("hotkey-two")

    first, second = await asyncio.gather(
        _admit(
            set_id=1,
            hotkey="hotkey-one",
            name="One",
            identity="one",
            source="same-source",
            funding=first_funding,
        ),
        _admit(
            set_id=1,
            hotkey="hotkey-two",
            name="Two",
            identity="two",
            source="same-source",
            funding=second_funding,
        ),
    )
    cross_funding = await _insert_credit("hotkey-three")
    cross = await _admit(
        set_id=2,
        hotkey="hotkey-three",
        name="Three",
        identity="three",
        source="same-source",
        funding=cross_funding,
    )

    async with _db.pool.acquire() as conn:
        jobs = await conn.fetch(
            """
            SELECT agent_id, set_id, status
            FROM pre_screening_jobs
            ORDER BY set_id, status
            """
        )
        credits = await conn.fetchval("SELECT count(*) FROM upload_credits WHERE redeemed_at IS NOT NULL")
        payments = await conn.fetchval("SELECT count(*) FROM evaluation_payments")
    assert {first.agent_id, second.agent_id, cross.agent_id} == {row["agent_id"] for row in jobs}
    assert [(row["set_id"], row["status"]) for row in jobs] == [
        (1, "failed"),
        (1, "pending"),
        (2, "pending"),
    ]
    assert credits == payments == 3


@pytest.mark.parametrize("kind", ["burn", "credit"])
async def test_concurrent_same_funding_has_current_replay_behavior(kind: str) -> None:
    await _insert_competition(1)
    funding = await _funding(kind, "hotkey", "same")
    calls = [
        _admit(
            set_id=1,
            hotkey="hotkey",
            name="Agent",
            identity="same",
            source="same-source",
            funding=funding,
        )
        for _ in range(2)
    ]
    outcomes = await asyncio.gather(*calls, return_exceptions=True)

    if kind == "burn":
        assert sum(not isinstance(outcome, Exception) for outcome in outcomes) == 1
        assert sum(isinstance(outcome, DuplicateAgentIDError) for outcome in outcomes) == 1
    else:
        assert all(not isinstance(outcome, Exception) for outcome in outcomes)
        assert {outcome.replayed for outcome in outcomes} == {False, True}
    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM agents") == 1
        assert await conn.fetchval("SELECT count(*) FROM evaluation_payments") == 1


@pytest.mark.parametrize("kind", ["burn", "credit"])
async def test_close_first_rejects_admission_without_consuming_funding(kind: str) -> None:
    await _insert_competition(1)
    funding = await _funding(kind, "hotkey", "close-first")
    blocker = await _db.pool.acquire()
    transaction = blocker.transaction()
    await transaction.start()
    committed = False
    try:
        await blocker.execute("SELECT set_id FROM competitions WHERE set_id = 1 FOR UPDATE")
        await blocker.execute("UPDATE competitions SET is_paused = TRUE WHERE set_id = 1")
        admission = asyncio.create_task(
            _admit(
                set_id=1,
                hotkey="hotkey",
                name="Agent",
                identity="close-first",
                source="source",
                funding=funding,
            )
        )
        _, pending = await asyncio.wait({admission}, timeout=0.1)
        assert admission in pending
        await transaction.commit()
        committed = True
        with pytest.raises(CompetitionNotAcceptingSubmissionsError):
            await admission
    finally:
        if not committed:
            await transaction.rollback()
        await _db.pool.release(blocker)

    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM agents") == 0
        if kind == "burn":
            assert await conn.fetchval("SELECT count(*) FROM evaluation_payments") == 0
        else:
            assert await conn.fetchval(
                "SELECT redeemed_at IS NULL FROM upload_credits WHERE credit_id = $1",
                funding.credit_id,
            )


@db_operation
async def _admit_while_holding_transaction(conn, *, entered, release, admission_kwargs):
    async with conn.conn.transaction():
        result = await _admit(**admission_kwargs)
        entered.set()
        await release.wait()
        return result


@pytest.mark.parametrize("kind", ["burn", "credit"])
async def test_admit_first_blocks_close_until_funding_is_consumed(kind: str) -> None:
    await _insert_competition(1)
    funding = await _funding(kind, "hotkey", "admit-first")
    entered = asyncio.Event()
    release = asyncio.Event()
    admission = asyncio.create_task(
        _admit_while_holding_transaction(
            entered=entered,
            release=release,
            admission_kwargs={
                "set_id": 1,
                "hotkey": "hotkey",
                "name": "Agent",
                "identity": "admit-first",
                "source": "source",
                "funding": funding,
            },
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=2)

    async def close_competition() -> None:
        async with _db.pool.acquire() as conn:
            await conn.execute("UPDATE competitions SET is_paused = TRUE WHERE set_id = 1")

    closing = asyncio.create_task(close_competition())
    _, pending = await asyncio.wait({closing}, timeout=0.1)
    assert closing in pending
    release.set()
    result = await admission
    await asyncio.wait_for(closing, timeout=2)

    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT agent_id FROM agents") == result.agent_id
        assert await conn.fetchval("SELECT agent_id FROM evaluation_payments") == result.agent_id
        assert await conn.fetchval("SELECT is_paused FROM competitions WHERE set_id = 1") is True


async def test_burn_reservation_rolls_back_and_same_funding_can_be_reused() -> None:
    await _insert_competition(1)
    funding = await _insert_burn("hotkey", "retry")
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO banned_coldkeys (miner_coldkey, banned_reason) VALUES ($1, 'test')",
            funding.miner_coldkey,
        )

    with pytest.raises(ColdkeyBannedError):
        await _admit(
            set_id=1,
            hotkey="hotkey",
            name="Agent",
            identity="retry",
            source="source",
            funding=funding,
        )
    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM evaluation_payments") == 0
        await conn.execute("DELETE FROM banned_coldkeys WHERE miner_coldkey = $1", funding.miner_coldkey)

    result = await _admit(
        set_id=1,
        hotkey="hotkey",
        name="Agent",
        identity="retry",
        source="source",
        funding=funding,
    )
    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT agent_id FROM evaluation_payments") == result.agent_id


async def test_matching_preexisting_burn_reservation_is_consumed() -> None:
    await _insert_competition(1)
    funding = await _insert_burn("hotkey", "reserved")
    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO evaluation_payments (
                payment_block_hash, payment_extrinsic_index, agent_id, miner_hotkey,
                miner_coldkey, amount_alpha_rao, quote_id
            ) VALUES ($1, $2, NULL, $3, $4, $5, $6)
            """,
            funding.payment_block_hash,
            funding.payment_extrinsic_index,
            funding.miner_hotkey,
            funding.miner_coldkey,
            funding.amount_alpha_rao,
            funding.quote_id,
        )

    result = await _admit(
        set_id=1,
        hotkey="hotkey",
        name="Agent",
        identity="reserved",
        source="source",
        funding=funding,
    )

    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT agent_id FROM evaluation_payments") == result.agent_id
