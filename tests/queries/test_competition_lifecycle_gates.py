from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

import utils.database as _db
from models.agent import AgentCreate, AgentStatus
from models.competition import (
    CompetitionAllocationUpdateRequest,
    CompetitionPolicy,
    CompetitionPolicyUpdateRequest,
    CompetitionStateUpdateRequest,
)
from models.evaluation_set import EvaluationSetGroup
from queries.agent import EvaluationCandidate, create_agent, get_evaluation_candidates_for_validator_hotkey
from queries.competition import (
    replace_competition_allocations,
    replace_competition_policy,
    update_competition_state,
)
from queries.errors import CompetitionAdminConflictError
from queries.evaluation import create_new_evaluation_and_evaluation_runs
from utils.database import DatabaseConnection

pytestmark = pytest.mark.anyio

ADMIN_ACTOR = "coldkey-ban-admin-api-key"

JUDGE_CLAIM_SQL = {
    "pre_screening_jobs": """
        SELECT job.job_id
        FROM pre_screening_jobs job
        INNER JOIN competitions competition ON competition.set_id = job.set_id
        WHERE competition.start_date IS NOT NULL
          AND competition.end_date IS NULL
          AND competition.is_paused IS FALSE
          AND EXISTS (
              SELECT 1
              FROM agents agent
              WHERE agent.agent_id = job.agent_id
                AND agent.set_id = job.set_id
          )
          AND (
              (job.status IN ('pending', 'error') AND job.next_attempt_at <= clock_timestamp())
              OR (job.status = 'running' AND job.lease_expires_at <= clock_timestamp())
          )
        ORDER BY job.created_at, job.job_id
        FOR SHARE OF competition SKIP LOCKED
        FOR UPDATE OF job SKIP LOCKED
        LIMIT 1
    """,
    "approval_jobs": """
        SELECT job.job_id
        FROM approval_jobs job
        INNER JOIN competitions competition ON competition.set_id = job.set_id
        WHERE competition.start_date IS NOT NULL
          AND competition.end_date IS NULL
          AND competition.is_paused IS FALSE
          AND EXISTS (
              SELECT 1
              FROM agents agent
              WHERE agent.agent_id = job.agent_id
                AND agent.set_id = job.set_id
          )
          AND (
              (job.status IN ('pending', 'error') AND job.next_attempt_at <= clock_timestamp())
              OR (job.status = 'running' AND job.lease_expires_at <= clock_timestamp())
          )
        ORDER BY job.created_at, job.job_id
        FOR SHARE OF competition SKIP LOCKED
        FOR UPDATE OF job SKIP LOCKED
        LIMIT 1
    """,
}


def _policy(**overrides) -> CompetitionPolicy:
    values = {
        "scoring_mode": "consensus",
        "screener_1_threshold": 0.4,
        "screener_2_threshold": 0.4,
        "prune_threshold": 0.4,
        "required_validator_count": 2,
        "pre_screening_enabled": True,
        "auto_approval_enabled": True,
        "hardcoding_policy_version": "hardcoding-v1",
        "incentive_enabled": False,
        "incentive_performance_threshold": 0.03,
        "incentive_cost_threshold": 0.06,
        "incentive_reward_half_life_hours": 336.0,
        "incentive_time_multiplier_scale_hours": 12.0,
    }
    values.update(overrides)
    return CompetitionPolicy(**values)


async def _seed_competition(
    conn,
    *,
    set_id: int,
    started: bool = True,
    paused: bool = False,
    ended: bool = False,
    configured: bool = True,
    draining: bool = False,
    weight: Decimal = Decimal("0"),
) -> None:
    await conn.executemany(
        """
        INSERT INTO evaluation_sets (set_id, set_group, problem_name)
        VALUES ($1, $2::evaluationsetgroup, $3)
        """,
        [(set_id, group, f"{set_id}-{group}") for group in ("screener_1", "screener_2", "validator")],
    )
    policy = _policy() if configured else None
    policy_values = (
        {column: None for column in CompetitionPolicy.model_fields} if policy is None else policy.model_dump()
    )
    closed_at = datetime.now(timezone.utc) - timedelta(days=2) if draining else None
    cutoff = closed_at + timedelta(hours=1) if closed_at is not None else None
    await conn.execute(
        """
        UPDATE competitions
        SET
            start_date = CASE WHEN $2 THEN clock_timestamp() - INTERVAL '7 days' ELSE NULL END,
            submissions_closed_at = $3,
            is_paused = $4,
            emissions_end_at = $5,
            end_date = CASE WHEN $6 THEN clock_timestamp() - INTERVAL '1 day' ELSE NULL END,
            raw_emission_weight = $7,
            scoring_mode = $8,
            screener_1_threshold = $9,
            screener_2_threshold = $10,
            prune_threshold = $11,
            required_validator_count = $12,
            pre_screening_enabled = $13,
            auto_approval_enabled = $14,
            hardcoding_policy_version = $15,
            incentive_enabled = $16,
            incentive_performance_threshold = $17,
            incentive_cost_threshold = $18,
            incentive_reward_half_life_hours = $19,
            incentive_time_multiplier_scale_hours = $20
        WHERE set_id = $1
        """,
        set_id,
        started,
        closed_at,
        paused,
        cutoff,
        ended,
        weight,
        *(policy_values[column] for column in CompetitionPolicy.model_fields),
    )


async def _insert_agent(
    conn,
    *,
    set_id: int,
    status: AgentStatus,
    created_at: datetime | None = None,
    hotkey: str | None = None,
    agent_id: UUID | None = None,
) -> UUID:
    agent_id = agent_id or uuid4()
    await conn.execute(
        """
        INSERT INTO agents (
            agent_id, miner_hotkey, name, version_num, status, created_at, ip_address, set_id
        ) VALUES ($1, $2, $2, 1, $3, $4, '127.0.0.1', $5)
        """,
        agent_id,
        hotkey or str(agent_id),
        status.value,
        created_at or datetime.now(timezone.utc),
        set_id,
    )
    return agent_id


async def _insert_judge_job(conn, *, table: str, set_id: int) -> UUID:
    agent_status = AgentStatus.pre_screening if table == "pre_screening_jobs" else AgentStatus.finished
    agent_id = await _insert_agent(conn, set_id=set_id, status=agent_status)
    if table == "pre_screening_jobs":
        await conn.execute(
            """
            INSERT INTO pre_screening_jobs (agent_id, set_id, status, policy_version)
            VALUES ($1, $2, 'pending', 'hardcoding-v1')
            """,
            agent_id,
            set_id,
        )
    else:
        await conn.execute(
            """
            INSERT INTO approval_jobs (agent_id, set_id, status, policy_version, input_snapshot)
            VALUES ($1, $2, 'pending', 'hardcoding-v1', '{}'::jsonb)
            """,
            agent_id,
            set_id,
        )
    return agent_id


async def _claim_judge_job(table: str):
    async with _db.pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(JUDGE_CLAIM_SQL[table])
            if row is None:
                return None
            await conn.execute(
                f"""
                UPDATE {table}
                SET status = 'running',
                    claimed_at = clock_timestamp(),
                    lease_expires_at = clock_timestamp() + INTERVAL '5 minutes'
                WHERE job_id = $1
                """,
                row["job_id"],
            )
            return row["job_id"]


def _state_target(**overrides) -> CompetitionStateUpdateRequest:
    values = {
        "started": True,
        "submissions_closed": False,
        "is_paused": False,
        "emissions_end_at": None,
        "ended": False,
        "reason": "lifecycle test",
    }
    values.update(overrides)
    return CompetitionStateUpdateRequest(**values)


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db):
    async with _db.pool.acquire() as conn:
        await conn.execute(
            """
            TRUNCATE
                competition_admin_events,
                approval_job_rounds,
                approval_jobs,
                agent_approval_states,
                pre_screening_results,
                pre_screening_jobs,
                evaluation_run_logs,
                evaluation_run_attempts,
                evaluation_runs,
                evaluations,
                agents,
                evaluation_sets,
                competitions
            RESTART IDENTITY CASCADE
            """
        )
        await conn.execute(
            """
            UPDATE competition_work_cursors
            SET last_served_set_id = NULL
            WHERE family IN ('screener_1', 'screener_2', 'validator')
            """
        )
    yield


@pytest.mark.parametrize(
    ("validator_hotkey", "status"),
    [
        ("screener-1-1", AgentStatus.screening_1),
        ("screener-2-1", AgentStatus.screening_2),
        ("validator-a", AgentStatus.evaluating),
    ],
)
async def test_discovery_skips_unprocessable_competitions_without_head_of_line_blocking(
    validator_hotkey: str,
    status: AgentStatus,
) -> None:
    now = datetime.now(timezone.utc)
    async with _db.pool.acquire() as conn:
        await _seed_competition(conn, set_id=1, paused=True)
        await _seed_competition(conn, set_id=2, started=False)
        await _seed_competition(conn, set_id=3, ended=True)
        await _seed_competition(conn, set_id=4, configured=False)
        await _seed_competition(conn, set_id=5, draining=True)
        await _seed_competition(conn, set_id=6)
        for index, set_id in enumerate((1, 2, 3, 4, 5, 6)):
            await _insert_agent(
                conn,
                set_id=set_id,
                status=status,
                created_at=now + timedelta(seconds=index),
            )

    batch = await get_evaluation_candidates_for_validator_hotkey(validator_hotkey)
    assert [candidate.set_id for candidate in batch.candidates] == [5, 6]


@pytest.mark.parametrize(
    ("validator_hotkey", "status"),
    [
        ("screener-1-1", AgentStatus.screening_1),
        ("screener-2-1", AgentStatus.screening_2),
        ("validator-a", AgentStatus.evaluating),
    ],
)
async def test_successful_issuance_rotates_competitions_cyclically(
    validator_hotkey: str,
    status: AgentStatus,
) -> None:
    async with _db.pool.acquire() as conn:
        for set_id in (40, 50, 60):
            await _seed_competition(conn, set_id=set_id)
            await _insert_agent(conn, set_id=set_id, status=status)
            await _insert_agent(conn, set_id=set_id, status=status)

    for expected_set_id in (40, 50, 60, 40):
        batch = await get_evaluation_candidates_for_validator_hotkey(validator_hotkey)
        assert batch.candidates[0].set_id == expected_set_id
        issued = await create_new_evaluation_and_evaluation_runs(
            batch.candidates[0],
            validator_hotkey,
            batch.observed_last_served_set_id,
        )
        assert issued is not None

    family = EvaluationSetGroup.from_validator_hotkey(validator_hotkey).value
    async with _db.pool.acquire() as conn:
        cursors = {
            row["family"]: row["last_served_set_id"]
            for row in await conn.fetch(
                "SELECT family, last_served_set_id FROM competition_work_cursors "
                "WHERE family IN ('screener_1', 'screener_2', 'validator')"
            )
        }
    assert cursors[family] == 40
    assert all(value is None for key, value in cursors.items() if key != family)


@pytest.mark.parametrize(
    ("validator_hotkey", "status"),
    [
        ("screener-1-1", AgentStatus.screening_1),
        ("screener-2-1", AgentStatus.screening_2),
        ("validator-a", AgentStatus.evaluating),
    ],
)
async def test_locked_competition_is_skipped_without_consuming_the_batch_observation(
    validator_hotkey: str,
    status: AgentStatus,
) -> None:
    async with _db.pool.acquire() as conn:
        await _seed_competition(conn, set_id=70)
        await _seed_competition(conn, set_id=80)
        await _insert_agent(conn, set_id=70, status=status)
        await _insert_agent(conn, set_id=80, status=status)

    batch = await get_evaluation_candidates_for_validator_hotkey(validator_hotkey)
    assert [candidate.set_id for candidate in batch.candidates] == [70, 80]

    async with _db.pool.acquire() as raw_conn:
        async with raw_conn.transaction():
            await raw_conn.execute("SELECT 1 FROM competitions WHERE set_id = 70 FOR UPDATE")
            assert (
                await asyncio.wait_for(
                    create_new_evaluation_and_evaluation_runs(
                        batch.candidates[0],
                        validator_hotkey,
                        batch.observed_last_served_set_id,
                    ),
                    timeout=2,
                )
                is None
            )
            issued = await create_new_evaluation_and_evaluation_runs(
                batch.candidates[1],
                validator_hotkey,
                batch.observed_last_served_set_id,
            )
            assert issued is not None

    family = EvaluationSetGroup.from_validator_hotkey(validator_hotkey).value
    async with _db.pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT last_served_set_id FROM competition_work_cursors WHERE family = $1",
                family,
            )
            == 80
        )


async def test_locked_agent_is_skipped_and_next_competition_can_issue() -> None:
    async with _db.pool.acquire() as conn:
        await _seed_competition(conn, set_id=85)
        await _seed_competition(conn, set_id=86)
        locked_agent_id = await _insert_agent(conn, set_id=85, status=AgentStatus.screening_1)
        await _insert_agent(conn, set_id=86, status=AgentStatus.screening_1)

    batch = await get_evaluation_candidates_for_validator_hotkey("screener-1-1")
    assert [candidate.set_id for candidate in batch.candidates] == [85, 86]

    async with _db.pool.acquire() as raw_conn:
        async with raw_conn.transaction():
            await raw_conn.execute("SELECT 1 FROM agents WHERE agent_id = $1 FOR UPDATE", locked_agent_id)
            assert (
                await asyncio.wait_for(
                    create_new_evaluation_and_evaluation_runs(
                        batch.candidates[0],
                        "screener-1-1",
                        batch.observed_last_served_set_id,
                    ),
                    timeout=2,
                )
                is None
            )
            assert (
                await create_new_evaluation_and_evaluation_runs(
                    batch.candidates[1],
                    "screener-1-1",
                    batch.observed_last_served_set_id,
                )
                is not None
            )

    async with _db.pool.acquire() as conn:
        assert (
            await conn.fetchval("SELECT last_served_set_id FROM competition_work_cursors WHERE family = 'screener_1'")
            == 86
        )


async def test_concurrent_stale_observation_allows_exactly_one_issuance() -> None:
    async with _db.pool.acquire() as conn:
        await _seed_competition(conn, set_id=90)
        await _seed_competition(conn, set_id=100)
        await _insert_agent(conn, set_id=90, status=AgentStatus.screening_1)
        await _insert_agent(conn, set_id=100, status=AgentStatus.screening_1)

    batch = await get_evaluation_candidates_for_validator_hotkey("screener-1-1")
    results = await asyncio.gather(
        *(
            create_new_evaluation_and_evaluation_runs(
                candidate,
                "screener-1-1",
                batch.observed_last_served_set_id,
            )
            for candidate in batch.candidates
        )
    )

    successful = [result for result in results if result is not None]
    assert len(successful) == 1
    issued_set_id = successful[0][0].set_id
    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM evaluations") == 1
        assert (
            await conn.fetchval("SELECT last_served_set_id FROM competition_work_cursors WHERE family = 'screener_1'")
            == issued_set_id
        )


async def test_failed_issuance_rolls_back_work_and_cursor(monkeypatch) -> None:
    async with _db.pool.acquire() as conn:
        await _seed_competition(conn, set_id=110)
        await _insert_agent(conn, set_id=110, status=AgentStatus.screening_1)

    batch = await get_evaluation_candidates_for_validator_hotkey("screener-1-1")
    monkeypatch.setattr(
        "queries.evaluation.create_evaluation_runs",
        AsyncMock(side_effect=RuntimeError("run insertion failed")),
    )

    with pytest.raises(RuntimeError, match="run insertion failed"):
        await create_new_evaluation_and_evaluation_runs(
            batch.candidates[0],
            "screener-1-1",
            batch.observed_last_served_set_id,
        )

    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM evaluations") == 0
        assert (
            await conn.fetchval("SELECT last_served_set_id FROM competition_work_cursors WHERE family = 'screener_1'")
            is None
        )


@pytest.mark.parametrize(
    ("validator_hotkey", "status", "set_group"),
    [
        ("screener-1-1", AgentStatus.screening_1, "screener_1"),
        ("screener-2-1", AgentStatus.screening_2, "screener_2"),
        ("validator-a", AgentStatus.evaluating, "validator"),
    ],
)
async def test_competition_without_family_tasks_does_not_advance_cursor(
    validator_hotkey: str,
    status: AgentStatus,
    set_group: str,
) -> None:
    async with _db.pool.acquire() as conn:
        await _seed_competition(conn, set_id=115)
        await _insert_agent(conn, set_id=115, status=status)
        await conn.execute(
            "DELETE FROM evaluation_sets WHERE set_id = 115 AND set_group = $1::evaluationsetgroup",
            set_group,
        )

    batch = await get_evaluation_candidates_for_validator_hotkey(validator_hotkey)
    assert batch.candidates[0].set_id == 115
    assert (
        await create_new_evaluation_and_evaluation_runs(
            batch.candidates[0],
            validator_hotkey,
            batch.observed_last_served_set_id,
        )
        is None
    )

    async with _db.pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT last_served_set_id FROM competition_work_cursors WHERE family = $1",
                set_group,
            )
            is None
        )


@pytest.mark.parametrize(
    ("validator_hotkey", "status"),
    [
        ("screener-1-1", AgentStatus.screening_1),
        ("screener-2-1", AgentStatus.screening_2),
        ("validator-a", AgentStatus.evaluating),
    ],
)
async def test_within_competition_order_is_created_at_then_agent_id(
    validator_hotkey: str,
    status: AgentStatus,
) -> None:
    now = datetime.now(timezone.utc)
    smaller_id = UUID(int=1)
    larger_id = UUID(int=2)
    async with _db.pool.acquire() as conn:
        await _seed_competition(conn, set_id=120)
        await _insert_agent(
            conn,
            set_id=120,
            status=status,
            created_at=now + timedelta(seconds=1),
            agent_id=smaller_id,
        )
        await _insert_agent(
            conn,
            set_id=120,
            status=status,
            created_at=now,
            agent_id=larger_id,
        )

    batch = await get_evaluation_candidates_for_validator_hotkey(validator_hotkey)
    assert batch.candidates[0].agent_id == larger_id

    async with _db.pool.acquire() as conn:
        await conn.execute("DELETE FROM agents WHERE set_id = 120")
        await _insert_agent(
            conn,
            set_id=120,
            status=status,
            created_at=now,
            agent_id=larger_id,
        )
        await _insert_agent(
            conn,
            set_id=120,
            status=status,
            created_at=now,
            agent_id=smaller_id,
        )

    batch = await get_evaluation_candidates_for_validator_hotkey(validator_hotkey)
    assert batch.candidates[0].agent_id == smaller_id


@pytest.mark.parametrize(
    ("validator_hotkey", "status", "set_group"),
    [
        ("screener-1-1", AgentStatus.screening_1, EvaluationSetGroup.screener_1),
        ("screener-2-1", AgentStatus.screening_2, EvaluationSetGroup.screener_2),
        ("validator-a", AgentStatus.evaluating, EvaluationSetGroup.validator),
    ],
)
async def test_admin_first_pause_prevents_evaluation_issuance(
    validator_hotkey: str,
    status: AgentStatus,
    set_group: EvaluationSetGroup,
) -> None:
    async with _db.pool.acquire() as conn:
        await _seed_competition(conn, set_id=10)
        agent_id = await _insert_agent(conn, set_id=10, status=status)
    batch = await get_evaluation_candidates_for_validator_hotkey(validator_hotkey)
    candidate = next(candidate for candidate in batch.candidates if candidate.agent_id == agent_id)

    async with _db.pool.acquire() as raw_conn:
        async with raw_conn.transaction():
            await raw_conn.execute("SELECT 1 FROM competitions WHERE set_id = 10 FOR UPDATE")
            issuance = await asyncio.wait_for(
                create_new_evaluation_and_evaluation_runs(
                    candidate,
                    validator_hotkey,
                    batch.observed_last_served_set_id,
                ),
                timeout=2,
            )
            assert issuance is None
            await raw_conn.execute("UPDATE competitions SET is_paused = true WHERE set_id = 10")

    async with _db.pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM evaluations WHERE agent_id = $1 AND evaluation_set_group = $2",
                agent_id,
                set_group.value,
            )
            == 0
        )


@pytest.mark.parametrize(
    ("validator_hotkey", "status"),
    [
        ("screener-1-1", AgentStatus.screening_1),
        ("screener-2-1", AgentStatus.screening_2),
        ("validator-a", AgentStatus.evaluating),
    ],
)
async def test_issuance_first_completes_then_pause_waits(
    validator_hotkey: str,
    status: AgentStatus,
) -> None:
    async with _db.pool.acquire() as conn:
        await _seed_competition(conn, set_id=11)
        agent_id = await _insert_agent(conn, set_id=11, status=status)
    batch = await get_evaluation_candidates_for_validator_hotkey(validator_hotkey)
    candidate = next(candidate for candidate in batch.candidates if candidate.agent_id == agent_id)

    async with _db.pool.acquire() as raw_conn:
        db_conn = DatabaseConnection(raw_conn, "issuance_first_test")
        async with raw_conn.transaction():
            token = _db._per_context_conn.set(db_conn)
            try:
                issued = await create_new_evaluation_and_evaluation_runs.__wrapped__(
                    db_conn,
                    candidate,
                    validator_hotkey,
                    batch.observed_last_served_set_id,
                )
            finally:
                _db._per_context_conn.reset(token)
            assert issued is not None

            pause = asyncio.create_task(
                update_competition_state(
                    set_id=11,
                    target=_state_target(is_paused=True),
                    actor=ADMIN_ACTOR,
                )
            )
            await asyncio.sleep(0.05)
            assert not pause.done()

    paused = await asyncio.wait_for(pause, timeout=2)
    assert paused.is_paused is True
    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM evaluations WHERE agent_id = $1", agent_id) == 1


async def test_end_waits_for_issuance_then_rechecks_readiness() -> None:
    async with _db.pool.acquire() as conn:
        await _seed_competition(conn, set_id=14)
        agent_id = await _insert_agent(conn, set_id=14, status=AgentStatus.screening_1)
    batch = await get_evaluation_candidates_for_validator_hotkey("screener-1-1")
    candidate = next(candidate for candidate in batch.candidates if candidate.agent_id == agent_id)

    async with _db.pool.acquire() as raw_conn:
        db_conn = DatabaseConnection(raw_conn, "issuance_before_end_test")
        async with raw_conn.transaction():
            token = _db._per_context_conn.set(db_conn)
            try:
                issued = await create_new_evaluation_and_evaluation_runs.__wrapped__(
                    db_conn,
                    candidate,
                    "screener-1-1",
                    batch.observed_last_served_set_id,
                )
            finally:
                _db._per_context_conn.reset(token)
            assert issued is not None

            ending = asyncio.create_task(
                update_competition_state(
                    set_id=14,
                    target=_state_target(ended=True),
                    actor=ADMIN_ACTOR,
                )
            )
            await asyncio.sleep(0.05)
            assert not ending.done()

    with pytest.raises(CompetitionAdminConflictError, match="unfinished correctness work"):
        await asyncio.wait_for(ending, timeout=2)
    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT end_date FROM competitions WHERE set_id = 14") is None
        assert await conn.fetchval("SELECT count(*) FROM evaluations WHERE agent_id = $1", agent_id) == 1


async def test_end_waits_for_admission_then_rechecks_readiness(monkeypatch) -> None:
    monkeypatch.setattr("queries.agent.upload_text_file_to_s3", AsyncMock())
    async with _db.pool.acquire() as conn:
        await _seed_competition(conn, set_id=18)

    agent = AgentCreate(
        miner_hotkey="claim-first-miner",
        name="claim-first-agent",
        version_num=1,
        created_at=datetime.now(timezone.utc),
        ip_address="127.0.0.1",
        payment_block_hash="claim-first-payment",
        payment_extrinsic_index="0",
    )
    async with _db.pool.acquire() as raw_conn:
        db_conn = DatabaseConnection(raw_conn, "admission_before_end_test")
        async with raw_conn.transaction():
            token = _db._per_context_conn.set(db_conn)
            try:
                agent_id = await create_agent.__wrapped__(
                    db_conn,
                    agent,
                    "print('claim first')\n",
                    source_sha256="claim-first-source",
                    runtime_openrouter_api_key_ciphertext=b"runtime",
                    management_openrouter_api_key_ciphertext=b"management",
                    openrouter_workspace_id="workspace",
                    openrouter_api_key_label="label",
                    openrouter_api_key_creator_user_id="creator",
                    openrouter_validated_at=datetime.now(timezone.utc),
                )
            finally:
                _db._per_context_conn.reset(token)

            ending = asyncio.create_task(
                update_competition_state(
                    set_id=18,
                    target=_state_target(ended=True),
                    actor=ADMIN_ACTOR,
                )
            )
            await asyncio.sleep(0.05)
            assert not ending.done()

    with pytest.raises(CompetitionAdminConflictError, match="unfinished correctness work"):
        await asyncio.wait_for(ending, timeout=2)
    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT end_date FROM competitions WHERE set_id = 18") is None
        admitted_agent = await conn.fetchrow(
            "SELECT set_id, status::text AS status FROM agents WHERE agent_id = $1",
            agent_id,
        )
        assert dict(admitted_agent) == {"set_id": 18, "status": AgentStatus.pre_screening.value}
        admitted_job = await conn.fetchrow(
            "SELECT set_id, status FROM pre_screening_jobs WHERE agent_id = $1",
            agent_id,
        )
        assert dict(admitted_job) == {"set_id": 18, "status": "pending"}


@pytest.mark.parametrize("table", ["pre_screening_jobs", "approval_jobs"])
async def test_admin_first_pause_prevents_judge_claim(table: str) -> None:
    async with _db.pool.acquire() as conn:
        await _seed_competition(conn, set_id=15)
        await _insert_judge_job(conn, table=table, set_id=15)

    async with _db.pool.acquire() as raw_conn:
        async with raw_conn.transaction():
            await raw_conn.execute("SELECT 1 FROM competitions WHERE set_id = 15 FOR UPDATE")
            claim = asyncio.create_task(_claim_judge_job(table))
            await asyncio.sleep(0.05)
            assert claim.done()
            assert await claim is None
            await raw_conn.execute("UPDATE competitions SET is_paused = true WHERE set_id = 15")

    async with _db.pool.acquire() as conn:
        assert await conn.fetchval(f"SELECT status FROM {table}") == "pending"


@pytest.mark.parametrize("table", ["pre_screening_jobs", "approval_jobs"])
async def test_judge_claim_first_completes_then_pause_waits(table: str) -> None:
    async with _db.pool.acquire() as conn:
        await _seed_competition(conn, set_id=16)
        await _insert_judge_job(conn, table=table, set_id=16)

    async with _db.pool.acquire() as raw_conn:
        async with raw_conn.transaction():
            job = await raw_conn.fetchrow(JUDGE_CLAIM_SQL[table])
            assert job is not None
            await raw_conn.execute(
                f"UPDATE {table} SET status = 'running' WHERE job_id = $1",
                job["job_id"],
            )
            pause = asyncio.create_task(
                update_competition_state(
                    set_id=16,
                    target=_state_target(is_paused=True),
                    actor=ADMIN_ACTOR,
                )
            )
            await asyncio.sleep(0.05)
            assert not pause.done()

    paused = await asyncio.wait_for(pause, timeout=2)
    assert paused.is_paused is True
    async with _db.pool.acquire() as conn:
        assert await conn.fetchval(f"SELECT status FROM {table}") == "running"


@pytest.mark.parametrize("table", ["pre_screening_jobs", "approval_jobs"])
async def test_end_waits_for_judge_claim_then_rechecks_readiness(table: str) -> None:
    async with _db.pool.acquire() as conn:
        await _seed_competition(conn, set_id=17)
        await _insert_judge_job(conn, table=table, set_id=17)

    async with _db.pool.acquire() as raw_conn:
        async with raw_conn.transaction():
            job = await raw_conn.fetchrow(JUDGE_CLAIM_SQL[table])
            assert job is not None
            await raw_conn.execute(
                f"UPDATE {table} SET status = 'running' WHERE job_id = $1",
                job["job_id"],
            )
            ending = asyncio.create_task(
                update_competition_state(
                    set_id=17,
                    target=_state_target(ended=True),
                    actor=ADMIN_ACTOR,
                )
            )
            await asyncio.sleep(0.05)
            assert not ending.done()

    with pytest.raises(CompetitionAdminConflictError, match="unfinished correctness work"):
        await asyncio.wait_for(ending, timeout=2)
    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT end_date FROM competitions WHERE set_id = 17") is None


@pytest.mark.parametrize(
    ("validator_hotkey", "status", "existing_status", "existing_error"),
    [
        ("screener-1-1", AgentStatus.screening_1, "pending", None),
        ("screener-2-1", AgentStatus.screening_2, "finished", None),
        ("validator-a", AgentStatus.evaluating, "pending", None),
    ],
)
async def test_final_candidate_recheck_rejects_existing_running_or_successful_work(
    validator_hotkey: str,
    status: AgentStatus,
    existing_status: str,
    existing_error: int | None,
) -> None:
    set_group = EvaluationSetGroup.from_validator_hotkey(validator_hotkey)
    async with _db.pool.acquire() as conn:
        await _seed_competition(conn, set_id=12)
        agent_id = await _insert_agent(conn, set_id=12, status=status)
        evaluation_id = uuid4()
        await conn.execute(
            """
            INSERT INTO evaluations (
                evaluation_id, agent_id, validator_hotkey, set_id, evaluation_set_group, created_at
            ) VALUES ($1, $2, $3, 12, $4, NOW())
            """,
            evaluation_id,
            agent_id,
            validator_hotkey,
            set_group.value,
        )
        await conn.execute(
            """
            INSERT INTO evaluation_runs (
                evaluation_run_id, evaluation_id, problem_name, status, error_code, created_at
            ) VALUES ($1, $2, 'existing', $3, $4, NOW())
            """,
            uuid4(),
            evaluation_id,
            existing_status,
            existing_error,
        )

    assert (
        await create_new_evaluation_and_evaluation_runs(
            EvaluationCandidate(agent_id=agent_id, set_id=12),
            validator_hotkey,
            None,
        )
        is None
    )
    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM evaluations WHERE agent_id = $1", agent_id) == 1


async def test_validator_final_recheck_enforces_per_validator_and_required_count() -> None:
    async with _db.pool.acquire() as conn:
        await _seed_competition(conn, set_id=13)
        agent_id = await _insert_agent(conn, set_id=13, status=AgentStatus.evaluating)
        for validator_hotkey in ("validator-a", "validator-b"):
            evaluation_id = uuid4()
            await conn.execute(
                """
                INSERT INTO evaluations (
                    evaluation_id, agent_id, validator_hotkey, set_id, evaluation_set_group, created_at
                ) VALUES ($1, $2, $3, 13, 'validator', NOW())
                """,
                evaluation_id,
                agent_id,
                validator_hotkey,
            )
            await conn.execute(
                """
                INSERT INTO evaluation_runs (
                    evaluation_run_id, evaluation_id, problem_name, status, created_at
                ) VALUES ($1, $2, 'existing', 'finished', NOW())
                """,
                uuid4(),
                evaluation_id,
            )

    candidate = EvaluationCandidate(agent_id=agent_id, set_id=13)
    assert await create_new_evaluation_and_evaluation_runs(candidate, "validator-a", None) is None
    assert await create_new_evaluation_and_evaluation_runs(candidate, "validator-c", None) is None


@pytest.mark.parametrize(
    "blocker",
    [
        "agent",
        "unfinished_evaluation",
        "unfinished_run",
        "pre_pending",
        "pre_unprojected_terminal",
        "pre_needs_review",
        "approval_pending",
        "approval_unprojected_completed",
        "approval_needs_review",
    ],
)
async def test_end_readiness_rejects_each_correctness_blocker(blocker: str) -> None:
    async with _db.pool.acquire() as conn:
        await _seed_competition(conn, set_id=20)
        status = AgentStatus.evaluating if blocker == "agent" else AgentStatus.failed_screening_1
        agent_id = await _insert_agent(conn, set_id=20, status=status)

        if blocker in {"unfinished_evaluation", "unfinished_run"}:
            evaluation_id = uuid4()
            await conn.execute(
                """
                INSERT INTO evaluations (
                    evaluation_id, agent_id, validator_hotkey, set_id,
                    evaluation_set_group, created_at, finished_at
                ) VALUES ($1, $2, 'validator-a', 20, 'validator', NOW(), $3)
                """,
                evaluation_id,
                agent_id,
                None if blocker == "unfinished_evaluation" else datetime.now(timezone.utc),
            )
            if blocker == "unfinished_run":
                await conn.execute(
                    """
                    INSERT INTO evaluation_runs (
                        evaluation_run_id, evaluation_id, problem_name, status, created_at
                    ) VALUES ($1, $2, 'problem', 'pending', NOW())
                    """,
                    uuid4(),
                    evaluation_id,
                )
        elif blocker.startswith("pre_"):
            status_by_blocker = {
                "pre_pending": "pending",
                "pre_unprojected_terminal": "failed",
                "pre_needs_review": "needs_review",
            }
            await conn.execute(
                """
                INSERT INTO pre_screening_jobs (
                    agent_id, set_id, status, policy_version, projected_at
                ) VALUES ($1, 20, $2, 'hardcoding-v1', $3)
                """,
                agent_id,
                status_by_blocker[blocker],
                None,
            )
        elif blocker.startswith("approval_"):
            status_by_blocker = {
                "approval_pending": "pending",
                "approval_unprojected_completed": "completed",
                "approval_needs_review": "needs_review",
            }
            await conn.execute(
                """
                INSERT INTO approval_jobs (
                    agent_id, set_id, status, policy_version, input_snapshot, projected_at
                ) VALUES ($1, 20, $2, 'hardcoding-v1', '{}'::jsonb, $3)
                """,
                agent_id,
                status_by_blocker[blocker],
                None,
            )

    with pytest.raises(CompetitionAdminConflictError, match="unfinished correctness work"):
        await update_competition_state(
            set_id=20,
            target=_state_target(ended=True),
            actor=ADMIN_ACTOR,
        )


async def test_projected_terminal_work_allows_end_even_if_notifications_are_unsent() -> None:
    async with _db.pool.acquire() as conn:
        await _seed_competition(conn, set_id=21)
        agent_id = await _insert_agent(conn, set_id=21, status=AgentStatus.failed_pre_screening)
        await conn.execute(
            """
            INSERT INTO pre_screening_jobs (
                agent_id, set_id, status, policy_version, projected_at, announcement_sent_at
            ) VALUES ($1, 21, 'failed', 'hardcoding-v1', NOW(), NULL)
            """,
            agent_id,
        )
        approval_agent = await _insert_agent(conn, set_id=21, status=AgentStatus.finished)
        await conn.execute(
            """
            INSERT INTO approval_jobs (
                agent_id, set_id, status, policy_version, input_snapshot,
                projected_at, announcement_sent_at
            ) VALUES ($1, 21, 'completed', 'hardcoding-v1', '{}'::jsonb, NOW(), NULL)
            """,
            approval_agent,
        )

    ended = await update_competition_state(
        set_id=21,
        target=_state_target(ended=True),
        actor=ADMIN_ACTOR,
    )
    assert ended.ended is True


async def test_policy_reads_remain_normal_read_committed_observations() -> None:
    async with _db.pool.acquire() as conn:
        await _seed_competition(conn, set_id=30)

    updated_policy = _policy(screener_1_threshold=0.87)
    request = CompetitionPolicyUpdateRequest(**updated_policy.model_dump(), reason="raise threshold")
    async with _db.pool.acquire() as reader:
        async with reader.transaction():
            assert await reader.fetchval(
                "SELECT screener_1_threshold::float8 FROM competitions WHERE set_id = 30"
            ) == pytest.approx(0.4)
            update = asyncio.create_task(replace_competition_policy(set_id=30, target=request, actor=ADMIN_ACTOR))
            await asyncio.wait_for(update, timeout=2)
            assert await reader.fetchval(
                "SELECT screener_1_threshold::float8 FROM competitions WHERE set_id = 30"
            ) == pytest.approx(0.87)


async def test_concurrent_allocation_updates_serialize_without_partial_vectors() -> None:
    async with _db.pool.acquire() as conn:
        await _seed_competition(conn, set_id=40)
        await _seed_competition(conn, set_id=41, paused=True)

    first = CompetitionAllocationUpdateRequest(
        allocations=[
            {"set_id": 40, "raw_emission_weight": Decimal("0.7")},
            {"set_id": 41, "raw_emission_weight": Decimal("0.2")},
        ],
        reason="first vector",
    )
    second = CompetitionAllocationUpdateRequest(
        allocations=[
            {"set_id": 40, "raw_emission_weight": Decimal("0.1")},
            {"set_id": 41, "raw_emission_weight": Decimal("0.3")},
        ],
        reason="second vector",
    )

    async with _db.pool.acquire() as raw_conn:
        async with raw_conn.transaction():
            await raw_conn.execute("SELECT 1 FROM competitions ORDER BY set_id FOR UPDATE")
            first_task = asyncio.create_task(replace_competition_allocations(target=first, actor=ADMIN_ACTOR))
            second_task = asyncio.create_task(replace_competition_allocations(target=second, actor=ADMIN_ACTOR))
            await asyncio.sleep(0.05)
            assert not first_task.done()
            assert not second_task.done()

    results = await asyncio.gather(first_task, second_task)
    async with _db.pool.acquire() as conn:
        stored = await conn.fetch("SELECT set_id, raw_emission_weight FROM competitions ORDER BY set_id")
        events = await conn.fetch("SELECT after_state FROM competition_admin_events ORDER BY created_at, event_id")
    stored_vector = [(row["set_id"], row["raw_emission_weight"]) for row in stored]
    possible_vectors = [
        [(allocation.set_id, allocation.raw_emission_weight) for allocation in result.allocations] for result in results
    ]
    assert stored_vector in possible_vectors
    assert len(events) == 2
    assert all(len(json.loads(event["after_state"])["allocations"]) == 2 for event in events)
