import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Dict, Optional
from uuid import UUID

import api.config as config
from models.competition import CompetitionPolicy
from utils.database import DatabaseConnection, db_operation
from utils.incentives import RewardCandidate


@dataclass(frozen=True, slots=True)
class LegacyWeightReceiver:
    agent_id: UUID
    miner_hotkey: str


@dataclass(frozen=True, slots=True)
class CompetitionWeightInput:
    set_id: int
    start_date: datetime | None
    is_paused: bool
    emissions_end_at: datetime | None
    end_date: datetime | None
    raw_emission_weight: Decimal
    policy: CompetitionPolicy | None
    legacy_receiver: LegacyWeightReceiver | None
    incentive_candidates: tuple[RewardCandidate, ...]


@dataclass(frozen=True, slots=True)
class WeightCalculationSnapshot:
    observed_at: datetime
    competitions: tuple[CompetitionWeightInput, ...]


_WEIGHT_POLICY_COLUMNS = tuple(CompetitionPolicy.model_fields)


def _weight_policy_from_row(row) -> CompetitionPolicy | None:
    values = {column: row[column] for column in _WEIGHT_POLICY_COLUMNS}
    if all(value is None for value in values.values()):
        return None
    return CompetitionPolicy.model_validate(values)


def _is_emission_active(row, observed_at: datetime) -> bool:
    return (
        row["start_date"] is not None
        and not row["is_paused"]
        and row["end_date"] is None
        and (row["emissions_end_at"] is None or observed_at < row["emissions_end_at"])
    )


async def _get_legacy_weight_receiver(
    conn: DatabaseConnection,
    *,
    set_id: int,
    observed_at: datetime,
) -> LegacyWeightReceiver | None:
    row = await conn.fetchrow(
        """
        WITH current_leader AS (
            SELECT
                score.miner_hotkey,
                score.agent_id,
                score.approved_at
            FROM agent_scores score
            INNER JOIN agents agent
                ON agent.agent_id = score.agent_id
                AND agent.set_id = score.set_id
            LEFT JOIN LATERAL (
                SELECT AVG(evaluation.avg_cost_usd) AS avg_cost_usd
                FROM evaluations_hydrated evaluation
                WHERE evaluation.agent_id = score.agent_id
                  AND evaluation.set_id = score.set_id
                  AND evaluation.evaluation_set_group = 'validator'::EvaluationSetGroup
                  AND evaluation.status = 'success'::EvaluationStatus
            ) runtime ON true
            WHERE score.set_id = $1
              AND score.approved IS TRUE
              AND score.approved_at <= $2
              AND score.status::text <> 'cancelled'
              AND NOT EXISTS (
                  SELECT 1
                  FROM benchmark_agent_ids benchmark
                  WHERE benchmark.agent_id = score.agent_id
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM banned_coldkeys banned
                  WHERE banned.miner_coldkey = agent.miner_coldkey
              )
            ORDER BY
                score.final_score DESC,
                runtime.avg_cost_usd ASC NULLS LAST,
                score.created_at ASC
            LIMIT 1
        )
        SELECT miner_hotkey, agent_id
        FROM current_leader
        WHERE approved_at >= $2 - INTERVAL '12 hours'
        """,
        set_id,
        observed_at,
    )
    if row is None:
        return None
    return LegacyWeightReceiver(agent_id=row["agent_id"], miner_hotkey=row["miner_hotkey"])


async def _get_incentive_reward_candidates(
    conn: DatabaseConnection,
    *,
    set_id: int,
    required_validator_count: int,
    observed_at: datetime,
) -> list[RewardCandidate]:
    rows = await conn.fetch(
        """
        SELECT
            approved.agent_id,
            agent.miner_hotkey,
            approved.relative_improvement_units,
            approved.time_multiplier,
            approved.initial_reward_score,
            approved.approved_at
        FROM approved_agents approved
        INNER JOIN agents agent
            ON agent.agent_id = approved.agent_id
            AND agent.set_id = approved.set_id
        INNER JOIN agent_scores score
            ON score.agent_id = approved.agent_id
            AND score.set_id = approved.set_id
        LEFT JOIN agent_final_review_statuses review
            ON review.agent_id = approved.agent_id
            AND review.set_id = approved.set_id
        WHERE approved.set_id = $1
          AND score.approved IS TRUE
          AND score.approved_at <= $3
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
        observed_at,
    )
    snapshot_fields = ("relative_improvement_units", "time_multiplier", "initial_reward_score")
    missing_snapshot_agent_ids = [
        str(row["agent_id"]) for row in rows if any(row[field] is None for field in snapshot_fields)
    ]
    if missing_snapshot_agent_ids:
        raise ValueError(
            f"Active incentive competition {set_id} has approved agents without incentive snapshots: "
            f"{', '.join(missing_snapshot_agent_ids)}"
        )

    for row in rows:
        snapshot_values = [float(row[field]) for field in snapshot_fields]
        if any(not math.isfinite(value) for value in snapshot_values):
            raise ValueError(f"Active incentive competition {set_id} has a non-finite reward snapshot")

        if row["relative_improvement_units"] < 0 or row["time_multiplier"] < 1 or row["initial_reward_score"] < 0:
            raise ValueError(f"Active incentive competition {set_id} has an invalid reward snapshot")

    return [
        RewardCandidate(
            agent_id=row["agent_id"],
            miner_hotkey=row["miner_hotkey"],
            initial_reward_score=float(row["initial_reward_score"]),
            approved_at=row["approved_at"],
        )
        for row in rows
    ]


@db_operation
async def get_weight_calculation_snapshot(conn: DatabaseConnection) -> WeightCalculationSnapshot:
    policy_select = ",\n            ".join(
        f"{column}::float8 AS {column}"
        if column
        in {
            "screener_1_threshold",
            "screener_2_threshold",
            "prune_threshold",
            "incentive_performance_threshold",
            "incentive_cost_threshold",
            "incentive_reward_half_life_hours",
            "incentive_time_multiplier_scale_hours",
        }
        else column
        for column in _WEIGHT_POLICY_COLUMNS
    )
    async with conn.conn.transaction(isolation="repeatable_read", readonly=True):
        observed_at = await conn.fetchval("SELECT transaction_timestamp()")
        rows = await conn.fetch(
            f"""
            SELECT
                set_id,
                start_date,
                is_paused,
                emissions_end_at,
                end_date,
                raw_emission_weight,
                {policy_select}
            FROM competitions
            ORDER BY set_id
            """
        )

        competitions: list[CompetitionWeightInput] = []
        for row in rows:
            policy = _weight_policy_from_row(row)
            raw_weight = row["raw_emission_weight"]
            active_positive = raw_weight.is_finite() and raw_weight > 0 and _is_emission_active(row, observed_at)
            legacy_receiver = None
            incentive_candidates: tuple[RewardCandidate, ...] = ()
            if active_positive and policy is not None:
                if policy.incentive_enabled:
                    incentive_candidates = tuple(
                        await _get_incentive_reward_candidates(
                            conn,
                            set_id=row["set_id"],
                            required_validator_count=policy.required_validator_count,
                            observed_at=observed_at,
                        )
                    )
                else:
                    legacy_receiver = await _get_legacy_weight_receiver(
                        conn,
                        set_id=row["set_id"],
                        observed_at=observed_at,
                    )

            competitions.append(
                CompetitionWeightInput(
                    set_id=row["set_id"],
                    start_date=row["start_date"],
                    is_paused=row["is_paused"],
                    emissions_end_at=row["emissions_end_at"],
                    end_date=row["end_date"],
                    raw_emission_weight=raw_weight,
                    policy=policy,
                    legacy_receiver=legacy_receiver,
                    incentive_candidates=incentive_candidates,
                )
            )

    return WeightCalculationSnapshot(observed_at=observed_at, competitions=tuple(competitions))


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
    observed_at = await conn.fetchval("SELECT transaction_timestamp()")
    candidates = await _get_incentive_reward_candidates(
        conn,
        set_id=set_id,
        required_validator_count=required_validator_count,
        observed_at=observed_at,
    )
    return candidates, observed_at
