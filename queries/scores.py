from datetime import datetime, timezone
from typing import Dict, Optional

import api.config as config
from utils.database import DatabaseConnection, db_operation
from utils.incentives import RewardCandidate


@db_operation
async def get_weight_receiving_agent_hotkey(conn: DatabaseConnection) -> Optional[str]:
    # TODO ADAM: this query has artifacts of the old approval concept, fix
    current_leader = await conn.fetchrow(
        """
        WITH current_leader AS (
            SELECT 
                ass.miner_hotkey AS miner_hotkey,
                ass.approved AS approved,
                ass.approved_at AS approved_at
            FROM agent_scores ass
            INNER JOIN agents a ON a.agent_id = ass.agent_id
            LEFT JOIN LATERAL (
                SELECT AVG(eh.avg_cost_usd) AS avg_cost_usd
                FROM evaluations_hydrated eh
                WHERE eh.agent_id           = ass.agent_id
                  AND eh.set_id             = ass.set_id
                  AND eh.evaluation_set_group = 'validator'::EvaluationSetGroup
                  AND eh.status             = 'success'::EvaluationStatus
            ) rt ON true
            WHERE
                ass.approved
                AND ass.approved_at <= NOW()
                AND ass.set_id = (SELECT MAX(set_id) FROM evaluation_sets)
                AND ass.status::text <> 'cancelled'
                AND ass.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
                AND NOT EXISTS (
                    SELECT 1
                    FROM banned_coldkeys bc
                    WHERE bc.miner_coldkey = a.miner_coldkey
                )
            ORDER BY ass.final_score DESC, rt.avg_cost_usd ASC NULLS LAST, ass.created_at ASC
            LIMIT 1
        )
        SELECT miner_hotkey
        FROM current_leader
        WHERE
            approved
            AND approved_at <= NOW()
            AND approved_at >= NOW() - INTERVAL '12 hours'
        """
    )
    if current_leader is None or "miner_hotkey" not in current_leader:
        return None
    return current_leader["miner_hotkey"]


@db_operation
async def get_weight_receiving_agent_info(conn: DatabaseConnection) -> Optional[Dict[str, str]]:
    current_leader = await conn.fetchrow(
        """
        WITH current_leader AS (
            SELECT 
                ass.miner_hotkey AS miner_hotkey,
                ass.agent_id AS agent_id,
                ass.approved AS approved,
                ass.approved_at AS approved_at
            FROM agent_scores ass
            INNER JOIN agents a ON a.agent_id = ass.agent_id
            LEFT JOIN LATERAL (
                SELECT AVG(eh.avg_cost_usd) AS avg_cost_usd
                FROM evaluations_hydrated eh
                WHERE eh.agent_id           = ass.agent_id
                  AND eh.set_id             = ass.set_id
                  AND eh.evaluation_set_group = 'validator'::EvaluationSetGroup
                  AND eh.status             = 'success'::EvaluationStatus
            ) rt ON true
            WHERE
                ass.approved
                AND ass.approved_at <= NOW()
                AND ass.set_id = (SELECT MAX(set_id) FROM evaluation_sets)
                AND ass.status::text <> 'cancelled'
                AND ass.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
                AND NOT EXISTS (
                    SELECT 1
                    FROM banned_coldkeys bc
                    WHERE bc.miner_coldkey = a.miner_coldkey
                )
            ORDER BY ass.final_score DESC, rt.avg_cost_usd ASC NULLS LAST, ass.created_at ASC
            LIMIT 1
        )
        SELECT
            miner_hotkey,
            agent_id
        FROM current_leader
        WHERE
            approved
            AND approved_at <= NOW()
            AND approved_at >= NOW() - INTERVAL '12 hours'
    """
    )
    if current_leader is None or "miner_hotkey" not in current_leader or "agent_id" not in current_leader:
        return None
    return current_leader


@db_operation
async def get_incentive_reward_candidates(
    conn: DatabaseConnection,
    set_id: int,
    required_validator_count: int = config.NUM_EVALS_PER_AGENT,
) -> tuple[list[RewardCandidate], datetime]:
    rows = await conn.fetch(
        """
        SELECT
            approved.agent_id,
            agent.miner_hotkey,
            approved.relative_improvement_units,
            approved.time_multiplier,
            approved.initial_reward_score,
            approved.approved_at,
            NOW() AS observed_at
        FROM approved_agents approved
        INNER JOIN agents agent ON agent.agent_id = approved.agent_id
        INNER JOIN agent_scores score
            ON score.agent_id = approved.agent_id
            AND score.set_id = approved.set_id
        LEFT JOIN agent_final_review_statuses review
            ON review.agent_id = approved.agent_id
            AND review.set_id = approved.set_id
        WHERE approved.set_id = $1
          AND score.approved IS TRUE
          AND score.approved_at <= NOW()
          AND score.validator_count = $2
          AND score.status::text = 'finished'
          AND review.approval_review_status IS DISTINCT FROM 'rejected'
          AND NOT EXISTS (
              SELECT 1
              FROM benchmark_agent_ids benchmark
              WHERE benchmark.agent_id = approved.agent_id
          )
          AND NOT EXISTS (
              SELECT 1
              FROM banned_coldkeys banned
              WHERE banned.miner_coldkey = agent.miner_coldkey
          )
        """,
        set_id,
        required_validator_count,
    )
    snapshot_fields = ("relative_improvement_units", "time_multiplier", "initial_reward_score")
    missing_snapshot_agent_ids = [
        str(row["agent_id"]) for row in rows if any(row[field] is None for field in snapshot_fields)
    ]
    if missing_snapshot_agent_ids:
        raise RuntimeError(
            f"Active incentive set {set_id} has approved agents without incentive snapshots: "
            f"{', '.join(missing_snapshot_agent_ids)}"
        )
    observed_at = rows[0]["observed_at"] if rows else datetime.now(timezone.utc)
    candidates = [
        RewardCandidate(
            agent_id=row["agent_id"],
            miner_hotkey=row["miner_hotkey"],
            initial_reward_score=row["initial_reward_score"],
            approved_at=row["approved_at"],
        )
        for row in rows
    ]
    return candidates, observed_at
