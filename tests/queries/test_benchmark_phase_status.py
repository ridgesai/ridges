from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

import api.config as config
import utils.database as _db
from models.agent import AgentCompetitionStatus
from queries.agent import get_public_agent_by_id
from queries.approval import project_next_approval_job_state
from queries.benchmark_phase import end_benchmark_phase, start_benchmark_phase

SET_ID = 74

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


async def _judged_agent(conn, *, hotkey: str, final_score: float) -> UUID:
    agent_id = uuid4()
    created_at = datetime.now(timezone.utc) - timedelta(days=1)
    await conn.execute(
        "INSERT INTO agents (agent_id, miner_hotkey, miner_coldkey, name, version_num, status, created_at, "
        "ip_address, set_id) VALUES ($1, $2, $2, $2, 1, 'finished', $3, '127.0.0.1', $4)",
        agent_id,
        hotkey,
        created_at,
        SET_ID,
    )
    await conn.execute(
        "INSERT INTO agent_scores (agent_id, miner_hotkey, name, version_num, created_at, status, set_id, "
        "approved, approved_at, validator_count, final_score) "
        "VALUES ($1, $2, $2, 1, $3, 'finished', $4, false, NULL, $5, $6)",
        agent_id,
        hotkey,
        created_at,
        SET_ID,
        config.NUM_EVALS_PER_AGENT,
        final_score,
    )
    await conn.execute(
        """
        INSERT INTO approval_jobs (
            job_id, agent_id, set_id, status, policy_version, input_snapshot,
            aggregate_verdict, aggregate_score, aggregate_confidence, aggregate_summary, decision_source
        ) VALUES ($1, $2, $3, 'completed', 'approval-v1', '{}'::jsonb, 'approved', 0.9, 0.9, 'judge approved', 'auto_judge')
        """,
        uuid4(),
        agent_id,
        SET_ID,
    )
    return agent_id


async def test_public_agent_status_follows_the_benchmark_phase():
    await start_benchmark_phase(set_id=SET_ID, actor="admin", reason="launch")
    async with _db.pool.acquire() as conn:
        held = await _judged_agent(conn, hotkey="held-hk", final_score=0.12)
        winner = await _judged_agent(conn, hotkey="winner-hk", final_score=0.16)
    assert await project_next_approval_job_state() is True
    assert await project_next_approval_job_state() is True

    pooled = await get_public_agent_by_id(held)
    assert pooled.competition_state.status is AgentCompetitionStatus.benchmark_pool
    assert pooled.competition_state.approved is False

    await end_benchmark_phase(set_id=SET_ID, winner_agent_id=None, actor="admin", reason="done")

    won = await get_public_agent_by_id(winner)
    not_picked = await get_public_agent_by_id(held)
    assert won.competition_state.status is AgentCompetitionStatus.approved
    assert won.competition_state.reward_basis == "benchmark"
    assert not_picked.competition_state.status is AgentCompetitionStatus.didnt_qualify
    assert not_picked.competition_state.reward_basis is None
