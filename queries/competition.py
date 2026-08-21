from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Mapping

import asyncpg

import api.config as config
from models.competition import CompetitionPolicy, CompetitionState, derive_competition_state
from utils.database import DatabaseConnection, db_operation

POLICY_COLUMNS = (
    "scoring_mode",
    "screener_1_threshold",
    "screener_2_threshold",
    "prune_threshold",
    "required_validator_count",
    "pre_screening_enabled",
    "auto_approval_enabled",
    "hardcoding_policy_version",
    "incentive_enabled",
    "incentive_performance_threshold",
    "incentive_cost_threshold",
    "incentive_reward_half_life_hours",
    "incentive_time_multiplier_scale_hours",
)

_COMPETITION_CONTEXT_SELECT = """
    SELECT
        set_id,
        start_date,
        submissions_closed_at,
        is_paused,
        end_date,
        scoring_mode,
        screener_1_threshold::float8 AS screener_1_threshold,
        screener_2_threshold::float8 AS screener_2_threshold,
        prune_threshold::float8 AS prune_threshold,
        required_validator_count,
        pre_screening_enabled,
        auto_approval_enabled,
        hardcoding_policy_version,
        incentive_enabled,
        incentive_performance_threshold::float8 AS incentive_performance_threshold,
        incentive_cost_threshold::float8 AS incentive_cost_threshold,
        incentive_reward_half_life_hours::float8 AS incentive_reward_half_life_hours,
        incentive_time_multiplier_scale_hours::float8 AS incentive_time_multiplier_scale_hours
    FROM competitions
"""


@dataclass(slots=True, frozen=True)
class CompetitionContext:
    set_id: int
    start_date: datetime | None
    submissions_closed_at: datetime | None
    is_paused: bool
    end_date: datetime | None
    policy: CompetitionPolicy | None

    @property
    def state(self) -> CompetitionState:
        return derive_competition_state(
            start_date=self.start_date,
            submissions_closed_at=self.submissions_closed_at,
            is_paused=self.is_paused,
            end_date=self.end_date,
        )


def _policy_from_row(row: Mapping[str, object]) -> CompetitionPolicy | None:
    values = {column: row[column] for column in POLICY_COLUMNS}
    if all(value is None for value in values.values()):
        return None
    return CompetitionPolicy.model_validate(values)


def _context_from_row(row: Mapping[str, object]) -> CompetitionContext:
    return CompetitionContext(
        set_id=int(row["set_id"]),
        start_date=row["start_date"],
        submissions_closed_at=row["submissions_closed_at"],
        is_paused=bool(row["is_paused"]),
        end_date=row["end_date"],
        policy=_policy_from_row(row),
    )


def current_competition_policy_defaults(
    set_id: int,
    scoring_mode: Literal["legacy", "consensus"] = "consensus",
) -> CompetitionPolicy:
    """Build the one-time N=1 policy; feature competitions use consensus."""
    return CompetitionPolicy(
        scoring_mode=scoring_mode,
        screener_1_threshold=config.SCREENER_1_THRESHOLD,
        screener_2_threshold=config.SCREENER_2_THRESHOLD,
        prune_threshold=config.PRUNE_THRESHOLD,
        required_validator_count=config.NUM_EVALS_PER_AGENT,
        pre_screening_enabled=config.PRE_SCREENING_JUDGE_ENABLED,
        auto_approval_enabled=config.AUTO_APPROVAL_ENABLED,
        hardcoding_policy_version=config.HARDCODING_POLICY_VERSION,
        incentive_enabled=set_id >= config.INCENTIVE_START_SET_ID,
        incentive_performance_threshold=config.INCENTIVE_PERFORMANCE_THRESHOLD,
        incentive_cost_threshold=config.INCENTIVE_COST_THRESHOLD,
        incentive_reward_half_life_hours=config.INCENTIVE_REWARD_HALF_LIFE_HOURS,
        incentive_time_multiplier_scale_hours=config.INCENTIVE_TIME_MULTIPLIER_SCALE_HOURS,
    )


async def _get_current_competition_context(
    conn: DatabaseConnection,
    *,
    for_update: bool,
) -> CompetitionContext | None:
    lock_clause = "FOR UPDATE" if for_update else ""
    row = await conn.fetchrow(
        f"""
        {_COMPETITION_CONTEXT_SELECT}
        WHERE set_id = (
            SELECT set_id
            FROM competitions
            WHERE start_date IS NOT NULL
            ORDER BY set_id DESC
            LIMIT 1
        )
        {lock_clause}
        """
    )
    return None if row is None else _context_from_row(row)


@db_operation
async def get_current_competition_context(conn: DatabaseConnection) -> CompetitionContext | None:
    return await _get_current_competition_context(conn, for_update=False)


@db_operation
async def lock_current_competition_context(conn: DatabaseConnection) -> CompetitionContext | None:
    return await _get_current_competition_context(conn, for_update=True)


@db_operation
async def get_competition_policy(conn: DatabaseConnection, set_id: int) -> CompetitionPolicy | None:
    row = await conn.fetchrow(
        """
        SELECT
            scoring_mode,
            screener_1_threshold::float8 AS screener_1_threshold,
            screener_2_threshold::float8 AS screener_2_threshold,
            prune_threshold::float8 AS prune_threshold,
            required_validator_count,
            pre_screening_enabled,
            auto_approval_enabled,
            hardcoding_policy_version,
            incentive_enabled,
            incentive_performance_threshold::float8 AS incentive_performance_threshold,
            incentive_cost_threshold::float8 AS incentive_cost_threshold,
            incentive_reward_half_life_hours::float8 AS incentive_reward_half_life_hours,
            incentive_time_multiplier_scale_hours::float8 AS incentive_time_multiplier_scale_hours
        FROM competitions
        WHERE set_id = $1
        """,
        set_id,
    )
    return None if row is None else _policy_from_row(row)


@db_operation
async def initialize_current_competition_policy(conn: DatabaseConnection) -> CompetitionContext | None:
    """Initialize the current N=1 competition once, preserving stored edits."""
    async with conn.conn.transaction():
        current = await _get_current_competition_context(conn, for_update=True)
        if current is None or current.policy is not None:
            return current

        policy = current_competition_policy_defaults(current.set_id)
        row = await conn.fetchrow(
            """
            UPDATE competitions
            SET
                scoring_mode = $2,
                screener_1_threshold = $3,
                screener_2_threshold = $4,
                prune_threshold = $5,
                required_validator_count = $6,
                pre_screening_enabled = $7,
                auto_approval_enabled = $8,
                hardcoding_policy_version = $9,
                incentive_enabled = $10,
                incentive_performance_threshold = $11,
                incentive_cost_threshold = $12,
                incentive_reward_half_life_hours = $13,
                incentive_time_multiplier_scale_hours = $14
            WHERE set_id = $1
            RETURNING
                set_id,
                start_date,
                submissions_closed_at,
                is_paused,
                end_date,
                scoring_mode,
                screener_1_threshold::float8 AS screener_1_threshold,
                screener_2_threshold::float8 AS screener_2_threshold,
                prune_threshold::float8 AS prune_threshold,
                required_validator_count,
                pre_screening_enabled,
                auto_approval_enabled,
                hardcoding_policy_version,
                incentive_enabled,
                incentive_performance_threshold::float8 AS incentive_performance_threshold,
                incentive_cost_threshold::float8 AS incentive_cost_threshold,
                incentive_reward_half_life_hours::float8 AS incentive_reward_half_life_hours,
                incentive_time_multiplier_scale_hours::float8 AS incentive_time_multiplier_scale_hours
            """,
            current.set_id,
            policy.scoring_mode,
            policy.screener_1_threshold,
            policy.screener_2_threshold,
            policy.prune_threshold,
            policy.required_validator_count,
            policy.pre_screening_enabled,
            policy.auto_approval_enabled,
            policy.hardcoding_policy_version,
            policy.incentive_enabled,
            policy.incentive_performance_threshold,
            policy.incentive_cost_threshold,
            policy.incentive_reward_half_life_hours,
            policy.incentive_time_multiplier_scale_hours,
        )
        return _context_from_row(row)


@db_operation
async def get_competition_for_set(conn: DatabaseConnection, set_id: int) -> asyncpg.Record | None:
    """Retrieve competition details for a specific evaluation set."""
    return await conn.fetchrow(
        """
        SELECT name AS competition_name, start_date AS competition_start_date, end_date AS competition_end_date
        FROM competitions
        WHERE set_id = $1
        """,
        set_id,
    )
