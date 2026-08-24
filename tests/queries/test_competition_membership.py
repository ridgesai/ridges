from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

import api.config as config
import utils.database as _db
from models.agent import AgentCreate
from models.competition import CompetitionPolicy, CompetitionState
from queries.agent import (
    EvaluationCandidate,
    create_agent,
    find_duplicate_source_agent_in_current_set,
    get_latest_agent_created_at_for_miner_hotkey_in_current_competition,
)
from queries.competition import (
    current_competition_policy_defaults,
    get_competition_policy,
    get_current_competition_context,
    initialize_current_competition_policy,
)
from queries.errors import CompetitionNotAcceptingSubmissionsError, EvaluationSetMembershipMismatchError
from queries.evaluation import create_new_evaluation_and_evaluation_runs

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db):
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE evaluation_run_attempts, evaluation_runs, evaluations, evaluation_sets, competitions, agents "
            "RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "UPDATE competition_work_cursors SET last_served_set_id = NULL "
            "WHERE family IN ('screener_1', 'screener_2', 'validator')"
        )
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE evaluation_run_attempts, evaluation_runs, evaluations, evaluation_sets, competitions, agents "
            "RESTART IDENTITY CASCADE"
        )


def _policy(**overrides) -> CompetitionPolicy:
    values = {
        "scoring_mode": "consensus",
        "screener_1_threshold": 0.41,
        "screener_2_threshold": 0.42,
        "prune_threshold": 0.43,
        "required_validator_count": 3,
        "pre_screening_enabled": False,
        "auto_approval_enabled": False,
        "hardcoding_policy_version": "policy-stored-v1",
        "incentive_enabled": False,
        "incentive_performance_threshold": 0.03,
        "incentive_cost_threshold": 0.06,
        "incentive_reward_half_life_hours": 336.0,
        "incentive_time_multiplier_scale_hours": 12.0,
    }
    values.update(overrides)
    return CompetitionPolicy(**values)


async def _insert_competition(
    conn,
    *,
    set_id: int,
    start_date: datetime | None,
    policy: CompetitionPolicy | None,
    submissions_closed_at: datetime | None = None,
    is_paused: bool = False,
    end_date: datetime | None = None,
) -> None:
    values = policy.model_dump() if policy is not None else {column: None for column in CompetitionPolicy.model_fields}
    emissions_end_at = submissions_closed_at + timedelta(hours=1) if submissions_closed_at is not None else None
    await conn.execute(
        """
        INSERT INTO competitions (
            set_id,
            start_date,
            submissions_closed_at,
            is_paused,
            emissions_end_at,
            end_date,
            scoring_mode,
            screener_1_threshold,
            screener_2_threshold,
            prune_threshold,
            required_validator_count,
            pre_screening_enabled,
            auto_approval_enabled,
            hardcoding_policy_version,
            incentive_enabled,
            incentive_performance_threshold,
            incentive_cost_threshold,
            incentive_reward_half_life_hours,
            incentive_time_multiplier_scale_hours
        )
        VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
            $11, $12, $13, $14, $15, $16, $17, $18, $19
        )
        """,
        set_id,
        start_date,
        submissions_closed_at,
        is_paused,
        emissions_end_at,
        end_date,
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


def _agent(*, payment: str) -> AgentCreate:
    return AgentCreate(
        miner_hotkey="miner-hotkey",
        name="agent",
        version_num=0,
        created_at=datetime.now(timezone.utc),
        ip_address="127.0.0.1",
        payment_block_hash=payment,
        payment_extrinsic_index="0",
    )


async def _create_agent(monkeypatch, *, payment: str = "block"):
    monkeypatch.setattr("queries.agent.upload_text_file_to_s3", AsyncMock())
    return await create_agent(
        _agent(payment=payment),
        "print('hello')\n",
        source_sha256=f"sha-{payment}",
        runtime_openrouter_api_key_ciphertext=b"runtime",
        management_openrouter_api_key_ciphertext=b"management",
        openrouter_workspace_id="workspace",
        openrouter_api_key_label="label",
        openrouter_api_key_creator_user_id="creator",
        openrouter_validated_at=datetime.now(timezone.utc),
    )


async def _insert_agent_row(
    conn,
    *,
    set_id: int | None,
    miner_hotkey: str = "miner-hotkey",
    source_sha256: str | None = None,
    created_at: datetime | None = None,
    status: str = "screening_1",
):
    agent_id = uuid4()

    async def insert() -> None:
        await conn.execute(
            """
            INSERT INTO agents (
                agent_id, miner_hotkey, name, version_num, status, created_at,
                ip_address, source_sha256, set_id
            )
            VALUES ($1, $2, 'agent', 0, $6, $3, '127.0.0.1', $4, $5)
            """,
            agent_id,
            miner_hotkey,
            created_at or datetime.now(timezone.utc),
            source_sha256,
            set_id,
            status,
        )

    if set_id is None:
        # Recreate a pre-migration row to verify the legacy read path. New NULL
        # memberships are rejected by the production trigger.
        async with conn.transaction():
            await conn.execute("ALTER TABLE agents DISABLE TRIGGER trg_agents_competition_membership")
            await insert()
            await conn.execute("ALTER TABLE agents ENABLE TRIGGER trg_agents_competition_membership")
    else:
        await insert()
    return agent_id


async def test_initializer_fills_policy_once_and_ignores_higher_draft(monkeypatch) -> None:
    started_at = datetime(2026, 8, 20, tzinfo=timezone.utc)
    async with _db.pool.acquire() as conn:
        await _insert_competition(conn, set_id=10, start_date=started_at, policy=None)
        await _insert_competition(conn, set_id=99, start_date=None, policy=None)

    monkeypatch.setattr(config, "SCREENER_1_THRESHOLD", 0.51)
    monkeypatch.setattr(config, "SCREENER_2_THRESHOLD", 0.52)
    monkeypatch.setattr(config, "PRUNE_THRESHOLD", 0.53)
    monkeypatch.setattr(config, "NUM_EVALS_PER_AGENT", 7)
    monkeypatch.setattr(config, "PRE_SCREENING_JUDGE_ENABLED", True)
    monkeypatch.setattr(config, "AUTO_APPROVAL_ENABLED", True)
    monkeypatch.setattr(config, "HARDCODING_POLICY_VERSION", "configured-v1")
    monkeypatch.setattr(config, "INCENTIVE_START_SET_ID", 10)
    monkeypatch.setattr(config, "INCENTIVE_PERFORMANCE_THRESHOLD", 0.04)
    monkeypatch.setattr(config, "INCENTIVE_COST_THRESHOLD", 0.07)
    monkeypatch.setattr(config, "INCENTIVE_REWARD_HALF_LIFE_HOURS", 100.0)
    monkeypatch.setattr(config, "INCENTIVE_TIME_MULTIPLIER_SCALE_HOURS", 8.0)
    initialized = await initialize_current_competition_policy()

    assert initialized is not None
    assert initialized.set_id == 10
    assert initialized.state is CompetitionState.open
    assert initialized.policy == CompetitionPolicy(
        scoring_mode="consensus",
        screener_1_threshold=0.51,
        screener_2_threshold=0.52,
        prune_threshold=0.53,
        required_validator_count=7,
        pre_screening_enabled=True,
        auto_approval_enabled=True,
        hardcoding_policy_version="configured-v1",
        incentive_enabled=True,
        incentive_performance_threshold=0.04,
        incentive_cost_threshold=0.07,
        incentive_reward_half_life_hours=100.0,
        incentive_time_multiplier_scale_hours=8.0,
    )
    assert await get_competition_policy(10) == initialized.policy
    assert await get_competition_policy(99) is None

    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE competitions
            SET screener_1_threshold = 0.87, hardcoding_policy_version = 'operator-edited-v2'
            WHERE set_id = 10
            """
        )
    monkeypatch.setattr(config, "SCREENER_1_THRESHOLD", 0.11)
    monkeypatch.setattr(config, "HARDCODING_POLICY_VERSION", "configured-v3")

    preserved = await initialize_current_competition_policy()

    assert preserved is not None
    assert preserved.policy is not None
    assert preserved.policy.screener_1_threshold == 0.87
    assert preserved.policy.hardcoding_policy_version == "operator-edited-v2"
    current = await get_current_competition_context()
    assert current is not None and current.set_id == 10


async def test_policy_defaults_allow_explicit_legacy_override() -> None:
    assert current_competition_policy_defaults(10, scoring_mode="legacy").scoring_mode == "legacy"


async def test_initializer_is_noop_without_a_started_competition() -> None:
    async with _db.pool.acquire() as conn:
        await _insert_competition(conn, set_id=99, start_date=None, policy=None)

    assert await initialize_current_competition_policy() is None
    assert await get_competition_policy(99) is None


async def test_admission_uses_locked_current_competition_policy(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    async with _db.pool.acquire() as conn:
        await _insert_competition(conn, set_id=5, start_date=now - timedelta(days=2), policy=_policy())
        await _insert_competition(
            conn,
            set_id=10,
            start_date=now - timedelta(days=1),
            policy=_policy(pre_screening_enabled=True, hardcoding_policy_version="stored-policy-v2"),
        )
        await _insert_competition(conn, set_id=99, start_date=None, policy=_policy())

    monkeypatch.setattr(config, "PRE_SCREENING_JUDGE_ENABLED", False)
    monkeypatch.setattr(config, "HARDCODING_POLICY_VERSION", "global-opposite")
    agent_id = await _create_agent(monkeypatch)

    async with _db.pool.acquire() as conn:
        agent = await conn.fetchrow("SELECT set_id, status FROM agents WHERE agent_id = $1", agent_id)
        job = await conn.fetchrow(
            "SELECT policy_version, status FROM pre_screening_jobs WHERE agent_id = $1",
            agent_id,
        )
    assert (agent["set_id"], agent["status"]) == (10, "pre_screening")
    assert (job["policy_version"], job["status"]) == ("stored-policy-v2", "pending")


@pytest.mark.parametrize("state", ["paused", "draining", "ended"])
async def test_admission_rejects_non_open_current_without_fallback(monkeypatch, state: str) -> None:
    now = datetime.now(timezone.utc)
    async with _db.pool.acquire() as conn:
        await _insert_competition(conn, set_id=5, start_date=now - timedelta(days=3), policy=_policy())
        await _insert_competition(
            conn,
            set_id=10,
            start_date=now - timedelta(days=2),
            policy=_policy(),
            is_paused=state == "paused",
            submissions_closed_at=now - timedelta(days=1) if state == "draining" else None,
            end_date=now if state == "ended" else None,
        )

    with pytest.raises(CompetitionNotAcceptingSubmissionsError) as exc:
        await _create_agent(monkeypatch, payment=f"block-{state}")

    assert exc.value.set_id == 10
    assert exc.value.state == state
    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM agents") == 0


async def test_admission_rechecks_open_state_after_competition_lock(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    async with _db.pool.acquire() as conn:
        await _insert_competition(conn, set_id=10, start_date=now - timedelta(days=1), policy=_policy())

    monkeypatch.setattr("queries.agent.upload_text_file_to_s3", AsyncMock())
    async with _db.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT 1 FROM competitions WHERE set_id = 10 FOR UPDATE")
            admission = asyncio.create_task(_create_agent(monkeypatch, payment="pause-race"))
            await asyncio.sleep(0.05)
            assert not admission.done()
            await conn.execute("UPDATE competitions SET is_paused = true WHERE set_id = 10")

    with pytest.raises(CompetitionNotAcceptingSubmissionsError):
        await asyncio.wait_for(admission, timeout=2)
    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM agents") == 0


async def test_cooldown_and_duplicate_checks_are_membership_bound() -> None:
    now = datetime.now(timezone.utc)
    async with _db.pool.acquire() as conn:
        await _insert_competition(conn, set_id=10, start_date=now - timedelta(days=2), policy=_policy())
        await _insert_competition(conn, set_id=99, start_date=None, policy=_policy())
        current_created_at = now - timedelta(hours=2)
        await _insert_agent_row(
            conn,
            set_id=10,
            created_at=current_created_at,
            source_sha256="shared-source",
        )
        await _insert_agent_row(
            conn,
            set_id=99,
            created_at=now - timedelta(hours=1),
            source_sha256="shared-source",
        )
        same_set_match = await _insert_agent_row(
            conn,
            set_id=10,
            miner_hotkey="duplicate-miner",
            created_at=now - timedelta(minutes=30),
            source_sha256="second-source",
        )
        await _insert_agent_row(
            conn,
            set_id=99,
            miner_hotkey="duplicate-miner",
            created_at=now - timedelta(minutes=20),
            source_sha256="second-source",
        )
        duplicate_candidate = await _insert_agent_row(
            conn,
            set_id=10,
            miner_hotkey="duplicate-miner",
            created_at=now - timedelta(minutes=10),
            source_sha256="second-source",
        )

    cooldown = await get_latest_agent_created_at_for_miner_hotkey_in_current_competition("miner-hotkey")
    duplicate = await find_duplicate_source_agent_in_current_set(duplicate_candidate)

    assert cooldown == current_created_at
    assert duplicate == same_set_match


async def test_evaluation_issuance_uses_agent_membership_and_rejects_conflicts() -> None:
    now = datetime.now(timezone.utc)
    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO evaluation_sets (set_id, set_group, problem_name, created_at)
            VALUES
                (10, 'validator', 'active-problem', $1),
                (99, 'validator', 'draft-problem', $1)
            """,
            now,
        )
        policy = _policy(required_validator_count=3).model_dump()
        await conn.execute(
            """
            UPDATE competitions
            SET
                start_date = CASE WHEN set_id = 10 THEN NOW() ELSE NULL END,
                scoring_mode = $1,
                screener_1_threshold = $2,
                screener_2_threshold = $3,
                prune_threshold = $4,
                required_validator_count = $5,
                pre_screening_enabled = $6,
                auto_approval_enabled = $7,
                hardcoding_policy_version = $8,
                incentive_enabled = $9,
                incentive_performance_threshold = $10,
                incentive_cost_threshold = $11,
                incentive_reward_half_life_hours = $12,
                incentive_time_multiplier_scale_hours = $13
            WHERE set_id IN (10, 99)
            """,
            *(policy[column] for column in CompetitionPolicy.model_fields),
        )
        active_agent = await _insert_agent_row(conn, set_id=10, status="evaluating")
        matching_agent = await _insert_agent_row(conn, set_id=10, miner_hotkey="matching", status="evaluating")
        conflicting_agent = await _insert_agent_row(conn, set_id=10, miner_hotkey="conflicting", status="evaluating")
        null_member = await _insert_agent_row(conn, set_id=None, miner_hotkey="legacy")

    evaluation, runs = await create_new_evaluation_and_evaluation_runs(
        EvaluationCandidate(agent_id=active_agent, set_id=10),
        "validator-hotkey",
        None,
    )

    assert evaluation.set_id == 10
    assert [run.problem_name for run in runs] == ["active-problem"]
    matching_override = await create_new_evaluation_and_evaluation_runs(
        EvaluationCandidate(agent_id=matching_agent, set_id=10),
        "matching-validator-hotkey",
        10,
    )
    assert matching_override is not None
    assert matching_override[0].set_id == 10

    async with _db.pool.acquire() as conn:
        await conn.execute("UPDATE competitions SET start_date = NOW() WHERE set_id = 99")

    with pytest.raises(EvaluationSetMembershipMismatchError):
        await create_new_evaluation_and_evaluation_runs(
            EvaluationCandidate(agent_id=conflicting_agent, set_id=99),
            "validator-hotkey",
            10,
        )
    assert (
        await create_new_evaluation_and_evaluation_runs(
            EvaluationCandidate(agent_id=null_member, set_id=10),
            "validator-hotkey",
            10,
        )
        is None
    )
    assert (
        await create_new_evaluation_and_evaluation_runs(
            EvaluationCandidate(agent_id=uuid4(), set_id=10),
            "validator-hotkey",
            10,
        )
        is None
    )

    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM evaluations WHERE agent_id = $1", conflicting_agent) == 0
        assert await conn.fetchval("SELECT count(*) FROM evaluations WHERE agent_id = $1", null_member) == 0
