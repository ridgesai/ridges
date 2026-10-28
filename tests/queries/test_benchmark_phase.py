import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

import api.config as config
import queries.benchmark_phase as benchmark_phase_queries
import utils.database as _db
from models.competition import CompetitionPolicyUpdateRequest
from queries.benchmark_phase import end_benchmark_phase, get_benchmark_phase, start_benchmark_phase
from queries.competition import get_competition_policy, replace_competition_policy
from queries.errors import CompetitionAdminConflictError
from queries.evaluation import AgentRankingProfile
from utils.incentives import calculate_benchmark_reward

SET_ID = 73

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
async def clean_tables(postgres_db, monkeypatch):
    monkeypatch.setattr(config, "INCENTIVE_START_SET_ID", SET_ID)
    async with _db.pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE approval_job_rounds, agent_approval_states, approval_jobs, approved_agents, agent_scores, "
            "banned_coldkeys, benchmark_phase_entries, competition_benchmark_phases, competition_admin_events, "
            "agents, competitions RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            """
            INSERT INTO competitions (
                set_id, start_date, scoring_mode, screener_1_threshold, screener_2_threshold,
                prune_threshold, required_validator_count, max_concurrent_evaluation_runs, pre_screening_enabled,
                auto_approval_enabled, hardcoding_policy_version, incentive_enabled,
                incentive_performance_threshold, incentive_cost_threshold,
                incentive_reward_half_life_hours, incentive_time_multiplier_scale_hours
            ) VALUES ($1, NOW() - INTERVAL '10 days', 'consensus', 0.4, 0.4, 0.4, $2, 8, true, true,
                      'hardcoding-v1', true, $3, $4, 336, $5)
            """,
            SET_ID,
            config.NUM_EVALS_PER_AGENT,
            config.INCENTIVE_PERFORMANCE_THRESHOLD,
            config.INCENTIVE_COST_THRESHOLD,
            config.INCENTIVE_TIME_MULTIPLIER_SCALE_HOURS,
        )
    yield


async def _agent(conn, *, hotkey: str, coldkey: str, final_score: float, approved_at: datetime | None = None) -> UUID:
    agent_id = uuid4()
    created_at = datetime.now(timezone.utc) - timedelta(days=1)
    await conn.execute(
        "INSERT INTO agents (agent_id, miner_hotkey, miner_coldkey, name, version_num, status, created_at, "
        "ip_address, set_id) VALUES ($1, $2, $3, $2, 1, 'finished', $4, '127.0.0.1', $5)",
        agent_id,
        hotkey,
        coldkey,
        created_at,
        SET_ID,
    )
    if approved_at is not None:
        await conn.execute(
            "INSERT INTO approved_agents (agent_id, set_id, approved_at, relative_improvement_units, "
            "time_multiplier, initial_reward_score) VALUES ($1, $2, $3, 1, 1, 0)",
            agent_id,
            SET_ID,
            approved_at,
        )
    await conn.execute(
        "INSERT INTO agent_scores (agent_id, miner_hotkey, name, version_num, created_at, status, set_id, "
        "approved, approved_at, validator_count, final_score) "
        "VALUES ($1, $2, $2, 1, $3, 'finished', $4, $5, $6, $7, $8)",
        agent_id,
        hotkey,
        created_at,
        SET_ID,
        approved_at is not None,
        approved_at,
        config.NUM_EVALS_PER_AGENT,
        final_score,
    )
    return agent_id


async def _enter(conn, phase_id: UUID, *agent_ids: UUID) -> None:
    for agent_id in agent_ids:
        await conn.execute(
            "INSERT INTO benchmark_phase_entries (phase_id, agent_id, set_id) VALUES ($1, $2, $3)",
            phase_id,
            agent_id,
            SET_ID,
        )


async def _ban(conn, coldkey: str) -> None:
    await conn.execute("INSERT INTO banned_coldkeys (miner_coldkey, banned_reason) VALUES ($1, 'test ban')", coldkey)


async def test_start_opens_an_audited_phase():
    snapshot = await start_benchmark_phase(set_id=SET_ID, actor="admin", reason="launch")
    assert snapshot.ended_at is None
    assert snapshot.pool == []
    async with _db.pool.acquire() as conn:
        event = await conn.fetchrow("SELECT operation, actor, reason FROM competition_admin_events")
    assert dict(event) == {"operation": "benchmark_phase_start", "actor": "admin", "reason": "launch"}


async def test_start_refuses_a_second_phase_no_incentives_or_an_ended_competition():
    await start_benchmark_phase(set_id=SET_ID, actor="admin", reason="launch")
    with pytest.raises(CompetitionAdminConflictError, match="benchmark_phase_active"):
        await start_benchmark_phase(set_id=SET_ID, actor="admin", reason="again")

    async with _db.pool.acquire() as conn:
        await conn.execute("TRUNCATE competition_benchmark_phases CASCADE")
        await conn.execute("UPDATE competitions SET incentive_enabled = false WHERE set_id = $1", SET_ID)
    with pytest.raises(CompetitionAdminConflictError, match="incentives_disabled"):
        await start_benchmark_phase(set_id=SET_ID, actor="admin", reason="launch")

    async with _db.pool.acquire() as conn:
        await conn.execute(
            "UPDATE competitions SET incentive_enabled = true, end_date = NOW() WHERE set_id = $1", SET_ID
        )
    with pytest.raises(CompetitionAdminConflictError, match="competition_ended"):
        await start_benchmark_phase(set_id=SET_ID, actor="admin", reason="launch")


async def test_end_defaults_to_the_top_eligible_agent_and_pays_absolute_points():
    phase = await start_benchmark_phase(set_id=SET_ID, actor="admin", reason="launch")
    async with _db.pool.acquire() as conn:
        await _ban(conn, "banned-ck")
        banned_top = await _agent(conn, hotkey="a", coldkey="banned-ck", final_score=0.2)
        winner = await _agent(conn, hotkey="b", coldkey="b-ck", final_score=0.16)
        runner_up = await _agent(conn, hotkey="c", coldkey="c-ck", final_score=0.12)
        await _enter(conn, phase.phase_id, banned_top, winner, runner_up)

    snapshot = await get_benchmark_phase(SET_ID)
    assert [entry.agent_id for entry in snapshot.pool] == [winner, runner_up, banned_top]
    assert snapshot.pool[2].ineligible_reason == "coldkey_banned"
    assert snapshot.default_winner_agent_id == winner

    ended = await end_benchmark_phase(set_id=SET_ID, winner_agent_id=None, actor="admin", reason="done")
    assert ended.winner_agent_id == winner
    assert ended.winner_reward.total_points == pytest.approx(16.0)
    async with _db.pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM approved_agents WHERE agent_id = $1", winner)
        operations = [
            r["operation"]
            for r in await conn.fetch("SELECT operation FROM competition_admin_events ORDER BY created_at")
        ]
        others = await conn.fetchval("SELECT count(*) FROM approved_agents WHERE agent_id <> $1", winner)
    assert row["benchmark_phase_id"] == phase.phase_id
    assert row["performance_delta"] is None
    assert row["cost_delta"] is None
    assert row["baseline_agent_id"] is None
    assert row["relative_improvement_units"] == pytest.approx(16.0)
    assert row["time_multiplier"] == 1
    assert row["initial_reward_score"] == pytest.approx(16.0)
    assert operations == ["benchmark_phase_start", "benchmark_phase_end"]
    assert others == 0


async def test_end_adds_cost_points_against_the_leader(monkeypatch):
    phase = await start_benchmark_phase(set_id=SET_ID, actor="admin", reason="launch")
    async with _db.pool.acquire() as conn:
        leader = await _agent(
            conn,
            hotkey="leader",
            coldkey="leader-ck",
            final_score=0.1,
            approved_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        winner = await _agent(conn, hotkey="w", coldkey="w-ck", final_score=0.16)
        await _enter(conn, phase.phase_id, winner)
    created = datetime.now(timezone.utc)
    monkeypatch.setattr(
        benchmark_phase_queries,
        "get_validator_agent_score_for_set",
        AsyncMock(return_value=AgentRankingProfile(final_score=0.16, avg_cost_usd=0.0726, created_at=created)),
    )
    monkeypatch.setattr(
        benchmark_phase_queries,
        "_get_ban_stable_leader",
        AsyncMock(
            return_value=AgentRankingProfile(final_score=0.1, avg_cost_usd=0.2539, created_at=created, agent_id=leader)
        ),
    )

    ended = await end_benchmark_phase(set_id=SET_ID, winner_agent_id=None, actor="admin", reason="done")

    expected = calculate_benchmark_reward(
        candidate_score=0.16, candidate_cost=0.0726, leader_cost=0.2539, cost_threshold=config.INCENTIVE_COST_THRESHOLD
    )
    async with _db.pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM approved_agents WHERE agent_id = $1", winner)
    assert row["baseline_agent_id"] == leader
    assert row["cost_delta"] == pytest.approx(expected.cost_delta)
    assert row["relative_improvement_units"] == pytest.approx(expected.total_points)
    assert ended.winner_reward.cost_points == pytest.approx(expected.cost_points)


async def test_end_override_must_be_an_eligible_pool_agent():
    phase = await start_benchmark_phase(set_id=SET_ID, actor="admin", reason="launch")
    async with _db.pool.acquire() as conn:
        await _ban(conn, "banned-ck")
        top = await _agent(conn, hotkey="a", coldkey="a-ck", final_score=0.3)
        banned = await _agent(conn, hotkey="b", coldkey="banned-ck", final_score=0.25)
        low = await _agent(conn, hotkey="c", coldkey="c-ck", final_score=0.1)
        outsider = await _agent(conn, hotkey="d", coldkey="d-ck", final_score=0.5)
        await _enter(conn, phase.phase_id, top, banned, low)

    for candidate in (outsider, banned):
        with pytest.raises(CompetitionAdminConflictError, match="winner_not_eligible"):
            await end_benchmark_phase(set_id=SET_ID, winner_agent_id=candidate, actor="admin", reason="done")

    ended = await end_benchmark_phase(set_id=SET_ID, winner_agent_id=low, actor="admin", reason="done")
    assert ended.winner_agent_id == low
    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM approved_agents WHERE agent_id = $1", top) == 0


async def test_end_with_an_empty_pool_has_no_winner():
    await start_benchmark_phase(set_id=SET_ID, actor="admin", reason="launch")
    ended = await end_benchmark_phase(set_id=SET_ID, winner_agent_id=None, actor="admin", reason="done")
    assert ended.ended_at is not None
    assert ended.winner_agent_id is None
    assert ended.winner_reward is None
    async with _db.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM approved_agents") == 0


async def test_end_without_an_active_phase_conflicts():
    with pytest.raises(CompetitionAdminConflictError, match="no_active_benchmark_phase"):
        await end_benchmark_phase(set_id=SET_ID, winner_agent_id=None, actor="admin", reason="done")


async def test_get_returns_none_without_a_phase():
    assert await get_benchmark_phase(SET_ID) is None


async def test_incentives_cannot_be_disabled_while_a_phase_is_active():
    """With incentives off, approvals would bypass the hold and the phase could not end."""
    await start_benchmark_phase(set_id=SET_ID, actor="admin", reason="launch")
    current = await get_competition_policy(SET_ID)
    target = CompetitionPolicyUpdateRequest(
        **{**current.model_dump(), "incentive_enabled": False, "reason": "turn off"}
    )
    with pytest.raises(CompetitionAdminConflictError, match="benchmark_phase_active"):
        await replace_competition_policy(set_id=SET_ID, target=target, actor="admin")
    assert (await get_competition_policy(SET_ID)).incentive_enabled is True

    await end_benchmark_phase(set_id=SET_ID, winner_agent_id=None, actor="admin", reason="done")
    await replace_competition_policy(set_id=SET_ID, target=target, actor="admin")
    assert (await get_competition_policy(SET_ID)).incentive_enabled is False


async def test_end_waits_for_a_concurrent_competition_change():
    """Ending locks the competition row, so a policy change cannot slip between scoring the pool and the winner."""
    await start_benchmark_phase(set_id=SET_ID, actor="admin", reason="launch")
    async with _db.pool.acquire() as holder:
        transaction = holder.transaction()
        await transaction.start()
        await holder.execute("SELECT 1 FROM competitions WHERE set_id = $1 FOR UPDATE", SET_ID)
        ending = asyncio.create_task(
            end_benchmark_phase(set_id=SET_ID, winner_agent_id=None, actor="admin", reason="done")
        )
        await asyncio.sleep(0.5)
        assert not ending.done(), "end must wait for the competition row"
        await transaction.rollback()
        ended = await asyncio.wait_for(ending, timeout=5)
    assert ended.ended_at is not None
