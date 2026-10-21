from datetime import datetime
from typing import Mapping
from uuid import UUID

import api.config as config
from models.agent import AgentStatus
from models.benchmark_phase import BenchmarkPhaseSnapshot, BenchmarkPoolEntry, BenchmarkWinnerReward
from models.competition import CompetitionPolicy
from queries.approval import _get_ban_stable_leader, lock_incentive_approval
from queries.banned_coldkey import get_banned_coldkey, lock_coldkey_ban_state
from queries.competition import (
    _active_benchmark_phase_id,
    _get_competition_policy,
    _insert_competition_admin_event,
)
from queries.errors import CompetitionAdminConflictError, CompetitionNotFoundError
from queries.evaluation import get_validator_agent_score_for_set
from utils.database import DatabaseConnection, db_operation
from utils.incentives import calculate_benchmark_reward


def _audit(row: Mapping[str, object]) -> dict[str, object]:
    ended_at = row["ended_at"]
    winner = row["winner_agent_id"]
    return {
        "phase_id": str(row["phase_id"]),
        "started_at": row["started_at"].isoformat(),
        "ended_at": None if ended_at is None else ended_at.isoformat(),
        "winner_agent_id": None if winner is None else str(winner),
    }


def _snapshot(
    row: Mapping[str, object],
    *,
    pool: list[BenchmarkPoolEntry],
    default_winner_agent_id: UUID | None = None,
    winner_reward: BenchmarkWinnerReward | None = None,
) -> BenchmarkPhaseSnapshot:
    return BenchmarkPhaseSnapshot(
        phase_id=row["phase_id"],
        set_id=row["set_id"],
        started_at=row["started_at"],
        started_by=row["started_by"],
        start_reason=row["start_reason"],
        ended_at=row["ended_at"],
        ended_by=row["ended_by"],
        end_reason=row["end_reason"],
        winner_agent_id=row["winner_agent_id"],
        default_winner_agent_id=default_winner_agent_id,
        pool=pool,
        winner_reward=winner_reward,
    )


async def _ranked_pool(
    conn: DatabaseConnection, *, phase_id: UUID, set_id: int, required_validator_count: int
) -> list[BenchmarkPoolEntry]:
    rows = await conn.fetch(
        """
        SELECT entry.agent_id, entry.entered_at, agent.name, agent.version_num, agent.miner_hotkey,
               agent.miner_coldkey, agent.status, agent.created_at
        FROM benchmark_phase_entries entry
        JOIN agents agent ON agent.agent_id = entry.agent_id
        WHERE entry.phase_id = $1
        """,
        phase_id,
    )
    candidates: list[tuple[BenchmarkPoolEntry, datetime]] = []
    for row in rows:
        score = await get_validator_agent_score_for_set(row["agent_id"], set_id, required_validator_count)
        reason = None
        if row["status"] != AgentStatus.finished.value:
            reason = "not_finished"

        elif score is None:
            reason = "incomplete_validator_score"

        elif row["miner_coldkey"] is not None and await get_banned_coldkey(row["miner_coldkey"]) is not None:
            reason = "coldkey_banned"

        entry = BenchmarkPoolEntry(
            agent_id=row["agent_id"],
            name=row["name"],
            version_num=row["version_num"],
            miner_hotkey=row["miner_hotkey"],
            final_score=None if score is None else score.final_score,
            avg_cost_usd=None if score is None else score.avg_cost_usd,
            entered_at=row["entered_at"],
            eligible=reason is None,
            ineligible_reason=reason,
        )
        candidates.append((entry, row["created_at"]))

    def rank(item: tuple[BenchmarkPoolEntry, datetime]) -> tuple:
        entry, created_at = item
        # The leader ordering: highest score, then lowest cost (missing last), then earliest upload.
        return (
            not entry.eligible,
            -(entry.final_score or 0.0),
            entry.avg_cost_usd is None,
            entry.avg_cost_usd or 0.0,
            created_at,
        )

    return [entry for entry, _ in sorted(candidates, key=rank)]


async def _required_validator_count(conn: DatabaseConnection, set_id: int) -> tuple[CompetitionPolicy | None, int]:
    policy = await _get_competition_policy(conn, set_id)
    return policy, config.NUM_EVALS_PER_AGENT if policy is None else policy.required_validator_count


async def _approve_winner(
    conn: DatabaseConnection,
    *,
    phase_id: UUID,
    set_id: int,
    winner: BenchmarkPoolEntry,
    policy: CompetitionPolicy,
) -> BenchmarkWinnerReward:
    coldkey = await conn.fetchval("SELECT miner_coldkey FROM agents WHERE agent_id = $1", winner.agent_id)
    if coldkey is not None:
        await lock_coldkey_ban_state(conn, coldkey)
        if await get_banned_coldkey(coldkey) is not None:
            raise CompetitionAdminConflictError("winner_not_eligible")

    leader = await _get_ban_stable_leader(conn, set_id, winner.agent_id, policy.required_validator_count)
    reward = calculate_benchmark_reward(
        candidate_score=winner.final_score,
        candidate_cost=winner.avg_cost_usd,
        leader_cost=None if leader is None else leader.avg_cost_usd,
        cost_threshold=policy.incentive_cost_threshold,
    )
    await conn.execute(
        """
        INSERT INTO approved_agents (
            agent_id, set_id, approved_at, baseline_agent_id, performance_delta, cost_delta,
            relative_improvement_units, time_multiplier, initial_reward_score, benchmark_phase_id
        ) VALUES ($1, $2, NOW(), $3, NULL, $4, $5, 1, $5, $6)
        """,
        winner.agent_id,
        set_id,
        None if leader is None else leader.agent_id,
        reward.cost_delta,
        reward.total_points,
        phase_id,
    )
    return BenchmarkWinnerReward(
        score_points=reward.score_points,
        cost_points=reward.cost_points,
        total_points=reward.total_points,
    )


@db_operation
async def start_benchmark_phase(
    conn: DatabaseConnection, *, set_id: int, actor: str, reason: str
) -> BenchmarkPhaseSnapshot:
    async with conn.conn.transaction():
        await lock_incentive_approval(conn, set_id)
        competition = await conn.fetchrow("SELECT end_date FROM competitions WHERE set_id = $1 FOR UPDATE", set_id)
        if competition is None:
            raise CompetitionNotFoundError(set_id)

        if competition["end_date"] is not None:
            raise CompetitionAdminConflictError("competition_ended")

        policy = await _get_competition_policy(conn, set_id)
        if policy is None or not policy.incentive_enabled:
            raise CompetitionAdminConflictError("incentives_disabled")

        if await _active_benchmark_phase_id(conn, set_id) is not None:
            raise CompetitionAdminConflictError("benchmark_phase_active")

        phase = await conn.fetchrow(
            "INSERT INTO competition_benchmark_phases (set_id, started_by, start_reason) "
            "VALUES ($1, $2, $3) RETURNING *",
            set_id,
            actor,
            reason,
        )
        await _insert_competition_admin_event(
            conn,
            operation="benchmark_phase_start",
            actor=actor,
            reason=reason,
            before_state={"set_id": set_id, "benchmark_phase": None},
            after_state={"set_id": set_id, "benchmark_phase": _audit(phase)},
        )
        return _snapshot(phase, pool=[])


@db_operation
async def end_benchmark_phase(
    conn: DatabaseConnection, *, set_id: int, winner_agent_id: UUID | None, actor: str, reason: str
) -> BenchmarkPhaseSnapshot:
    async with conn.conn.transaction():
        await lock_incentive_approval(conn, set_id)
        if await conn.fetchval("SELECT 1 FROM competitions WHERE set_id = $1 FOR UPDATE", set_id) is None:
            raise CompetitionNotFoundError(set_id)

        phase = await conn.fetchrow(
            "SELECT * FROM competition_benchmark_phases WHERE set_id = $1 AND ended_at IS NULL FOR UPDATE",
            set_id,
        )
        if phase is None:
            raise CompetitionAdminConflictError("no_active_benchmark_phase")

        policy, required = await _required_validator_count(conn, set_id)
        if policy is None or not policy.incentive_enabled:
            raise CompetitionAdminConflictError("incentives_disabled")

        pool = await _ranked_pool(conn, phase_id=phase["phase_id"], set_id=set_id, required_validator_count=required)
        if winner_agent_id is None:
            winner = next((entry for entry in pool if entry.eligible), None)

        else:
            winner = next((entry for entry in pool if entry.agent_id == winner_agent_id), None)
            if winner is None or not winner.eligible:
                raise CompetitionAdminConflictError("winner_not_eligible")

        reward = None
        if winner is not None:
            reward = await _approve_winner(
                conn, phase_id=phase["phase_id"], set_id=set_id, winner=winner, policy=policy
            )

        ended = await conn.fetchrow(
            """
            UPDATE competition_benchmark_phases
            SET ended_at = NOW(), ended_by = $2, end_reason = $3, winner_agent_id = $4
            WHERE phase_id = $1
            RETURNING *
            """,
            phase["phase_id"],
            actor,
            reason,
            None if winner is None else winner.agent_id,
        )
        await _insert_competition_admin_event(
            conn,
            operation="benchmark_phase_end",
            actor=actor,
            reason=reason,
            before_state={"set_id": set_id, "benchmark_phase": _audit(phase)},
            after_state={
                "set_id": set_id,
                "benchmark_phase": _audit(ended),
                "winner_reward": None if reward is None else reward.model_dump(),
            },
        )
        return _snapshot(ended, pool=pool, winner_reward=reward)


@db_operation
async def get_benchmark_phase(conn: DatabaseConnection, set_id: int) -> BenchmarkPhaseSnapshot | None:
    if await conn.fetchval("SELECT 1 FROM competitions WHERE set_id = $1", set_id) is None:
        raise CompetitionNotFoundError(set_id)
    phase = await conn.fetchrow(
        "SELECT * FROM competition_benchmark_phases WHERE set_id = $1 ORDER BY started_at DESC LIMIT 1",
        set_id,
    )
    if phase is None:
        return None

    _, required = await _required_validator_count(conn, set_id)
    pool = await _ranked_pool(conn, phase_id=phase["phase_id"], set_id=set_id, required_validator_count=required)
    default_winner = None
    if phase["ended_at"] is None:
        default_winner = next((entry.agent_id for entry in pool if entry.eligible), None)
    return _snapshot(phase, pool=pool, default_winner_agent_id=default_winner)
