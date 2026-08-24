from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, Mapping

import asyncpg

import api.config as config
from models.competition import (
    CompetitionAdminSnapshot,
    CompetitionAllocation,
    CompetitionAllocationSnapshot,
    CompetitionAllocationUpdateRequest,
    CompetitionPolicy,
    CompetitionPolicyUpdateRequest,
    CompetitionState,
    CompetitionStateUpdateRequest,
    derive_competition_state,
)
from queries.errors import CompetitionAdminConflictError, CompetitionNotFoundError
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

_ADMIN_COMPETITION_SELECT = """
    SELECT
        set_id,
        start_date,
        submissions_closed_at,
        is_paused,
        emissions_end_at,
        end_date,
        raw_emission_weight,
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


def _admin_snapshot_from_row(row: Mapping[str, object]) -> CompetitionAdminSnapshot:
    start_date = row["start_date"]
    submissions_closed_at = row["submissions_closed_at"]
    end_date = row["end_date"]
    return CompetitionAdminSnapshot(
        set_id=int(row["set_id"]),
        state=derive_competition_state(
            start_date=start_date,
            submissions_closed_at=submissions_closed_at,
            is_paused=bool(row["is_paused"]),
            end_date=end_date,
        ),
        started=start_date is not None,
        start_date=start_date,
        submissions_closed=submissions_closed_at is not None,
        submissions_closed_at=submissions_closed_at,
        is_paused=bool(row["is_paused"]),
        emissions_end_at=row["emissions_end_at"],
        ended=end_date is not None,
        end_date=end_date,
        raw_emission_weight=row["raw_emission_weight"],
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


async def _get_competition_policy(conn: DatabaseConnection, set_id: int) -> CompetitionPolicy | None:
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
async def get_competition_policy(conn: DatabaseConnection, set_id: int) -> CompetitionPolicy | None:
    return await _get_competition_policy(conn, set_id)


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


async def _insert_competition_admin_event(
    conn: DatabaseConnection,
    *,
    operation: Literal["state", "policy", "allocation"],
    actor: str,
    reason: str,
    before_state: dict[str, object],
    after_state: dict[str, object],
) -> None:
    await conn.execute(
        """
        INSERT INTO competition_admin_events (
            operation,
            actor,
            reason,
            before_state,
            after_state
        ) VALUES ($1, $2, $3, $4::jsonb, $5::jsonb)
        """,
        operation,
        actor,
        reason,
        json.dumps(before_state, sort_keys=True),
        json.dumps(after_state, sort_keys=True),
    )


def _audit_snapshot(snapshot: CompetitionAdminSnapshot | CompetitionAllocationSnapshot) -> dict[str, object]:
    return snapshot.model_dump(mode="json")


async def _competition_has_end_blockers(conn: DatabaseConnection, set_id: int) -> bool:
    """Check all correctness blockers in one READ COMMITTED statement snapshot."""

    return await conn.fetchval(
        """
        SELECT
            EXISTS (
                SELECT 1
                FROM agents agent
                WHERE agent.set_id = $1
                  AND agent.status::text IN (
                      'pre_screening',
                      'pre_screening_needs_review',
                      'screening_1',
                      'screening_2',
                      'evaluating'
                  )
            )
            OR EXISTS (
                SELECT 1
                FROM evaluations evaluation
                WHERE evaluation.set_id = $1
                  AND (
                      evaluation.finished_at IS NULL
                      OR EXISTS (
                          SELECT 1
                          FROM evaluation_runs evaluation_run
                          WHERE evaluation_run.evaluation_id = evaluation.evaluation_id
                            AND evaluation_run.status::text NOT IN ('finished', 'error')
                      )
                  )
            )
            OR EXISTS (
                SELECT 1
                FROM pre_screening_jobs job
                WHERE job.set_id = $1
                  AND (
                      job.status IN ('pending', 'running', 'error', 'needs_review')
                      OR (
                          job.status IN ('succeeded', 'failed', 'needs_review')
                          AND job.projected_at IS NULL
                      )
                  )
            )
            OR EXISTS (
                SELECT 1
                FROM approval_jobs job
                WHERE job.set_id = $1
                  AND (
                      job.status IN ('pending', 'running', 'error', 'needs_review')
                      OR (job.status = 'completed' AND job.projected_at IS NULL)
                  )
            )
        """,
        set_id,
    )


async def _has_required_opening_tasks(conn: DatabaseConnection, set_id: int) -> bool:
    return await conn.fetchval(
        """
        SELECT
            EXISTS (
                SELECT 1 FROM evaluation_sets
                WHERE set_id = $1 AND set_group = 'screener_1'::evaluationsetgroup
            )
            AND EXISTS (
                SELECT 1 FROM evaluation_sets
                WHERE set_id = $1 AND set_group = 'screener_2'::evaluationsetgroup
            )
            AND EXISTS (
                SELECT 1 FROM evaluation_sets
                WHERE set_id = $1 AND set_group = 'validator'::evaluationsetgroup
            )
        """,
        set_id,
    )


def _validate_state_target(
    current: CompetitionAdminSnapshot,
    target: CompetitionStateUpdateRequest,
) -> None:
    if current.ended:
        raise CompetitionAdminConflictError(f"Competition {current.set_id} has ended and its state is terminal")

    if current.started and not target.started:
        raise CompetitionAdminConflictError(f"Competition {current.set_id} cannot return to draft")

    if not target.started and (target.is_paused or target.submissions_closed):
        raise CompetitionAdminConflictError("A draft or cancelled competition cannot be paused or closed")


def _state_target_matches(current: CompetitionAdminSnapshot, target: CompetitionStateUpdateRequest) -> bool:
    return (
        current.started == target.started
        and current.submissions_closed == target.submissions_closed
        and current.is_paused == target.is_paused
        and current.emissions_end_at == target.emissions_end_at
        and current.ended == target.ended
    )


@db_operation
async def update_competition_state(
    conn: DatabaseConnection,
    *,
    set_id: int,
    target: CompetitionStateUpdateRequest,
    actor: str,
) -> CompetitionAdminSnapshot:
    async with conn.conn.transaction():
        row = await conn.fetchrow(
            f"""
            {_ADMIN_COMPETITION_SELECT}
            WHERE set_id = $1
            FOR UPDATE
            """,
            set_id,
        )
        if row is None:
            raise CompetitionNotFoundError(set_id)

        before = _admin_snapshot_from_row(row)
        if _state_target_matches(before, target):
            return before

        _validate_state_target(before, target)

        observed_at = await conn.fetchval("SELECT clock_timestamp()")
        start_date = before.start_date if before.started else observed_at if target.started else None
        submissions_closed_at = (
            before.submissions_closed_at
            if target.submissions_closed and before.submissions_closed
            else observed_at
            if target.submissions_closed
            else None
        )
        end_date = before.end_date if before.ended else observed_at if target.ended else None

        if target.emissions_end_at is not None and target.emissions_end_at < submissions_closed_at:
            raise CompetitionAdminConflictError("emissions_end_at cannot be before submissions close")

        policy = before.policy
        if target.started and not before.started:
            if not await _has_required_opening_tasks(conn, set_id):
                raise CompetitionAdminConflictError(
                    "Opening requires at least one screener_1, screener_2, and validator task"
                )
            if policy is None:
                policy = current_competition_policy_defaults(set_id)

        if target.ended and not before.ended and await _competition_has_end_blockers(conn, set_id):
            raise CompetitionAdminConflictError(f"Competition {set_id} still has unfinished correctness work")

        policy_values = {column: None for column in POLICY_COLUMNS} if policy is None else policy.model_dump()
        await conn.execute(
            """
            UPDATE competitions
            SET
                start_date = $2,
                submissions_closed_at = $3,
                is_paused = $4,
                emissions_end_at = $5,
                end_date = $6,
                scoring_mode = $7,
                screener_1_threshold = $8,
                screener_2_threshold = $9,
                prune_threshold = $10,
                required_validator_count = $11,
                pre_screening_enabled = $12,
                auto_approval_enabled = $13,
                hardcoding_policy_version = $14,
                incentive_enabled = $15,
                incentive_performance_threshold = $16,
                incentive_cost_threshold = $17,
                incentive_reward_half_life_hours = $18,
                incentive_time_multiplier_scale_hours = $19
            WHERE set_id = $1
            """,
            set_id,
            start_date,
            submissions_closed_at,
            target.is_paused,
            target.emissions_end_at,
            end_date,
            *(policy_values[column] for column in POLICY_COLUMNS),
        )
        updated_row = await conn.fetchrow(
            f"""
            {_ADMIN_COMPETITION_SELECT}
            WHERE set_id = $1
            """,
            set_id,
        )
        after = _admin_snapshot_from_row(updated_row)
        await _insert_competition_admin_event(
            conn,
            operation="state",
            actor=actor,
            reason=target.reason,
            before_state=_audit_snapshot(before),
            after_state=_audit_snapshot(after),
        )
        return after


@db_operation
async def replace_competition_policy(
    conn: DatabaseConnection,
    *,
    set_id: int,
    target: CompetitionPolicyUpdateRequest,
    actor: str,
) -> CompetitionAdminSnapshot:
    policy = CompetitionPolicy.model_validate(target.model_dump(exclude={"reason"}))
    async with conn.conn.transaction():
        row = await conn.fetchrow(
            f"""
            {_ADMIN_COMPETITION_SELECT}
            WHERE set_id = $1
            FOR UPDATE
            """,
            set_id,
        )
        if row is None:
            raise CompetitionNotFoundError(set_id)

        before = _admin_snapshot_from_row(row)
        if before.policy == policy:
            return before

        policy_values = policy.model_dump()
        await conn.execute(
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
            """,
            set_id,
            *(policy_values[column] for column in POLICY_COLUMNS),
        )
        updated_row = await conn.fetchrow(
            f"""
            {_ADMIN_COMPETITION_SELECT}
            WHERE set_id = $1
            """,
            set_id,
        )
        after = _admin_snapshot_from_row(updated_row)
        await _insert_competition_admin_event(
            conn,
            operation="policy",
            actor=actor,
            reason=target.reason,
            before_state=_audit_snapshot(before),
            after_state=_audit_snapshot(after),
        )
        return after


def _allocation_snapshot(allocations: Mapping[int, Decimal]) -> CompetitionAllocationSnapshot:
    sorted_allocations = [
        CompetitionAllocation(set_id=set_id, raw_emission_weight=weight)
        for set_id, weight in sorted(allocations.items())
    ]
    return CompetitionAllocationSnapshot(
        allocations=sorted_allocations,
        owner_emission_weight=Decimal("1") - sum(allocations.values(), Decimal("0")),
    )


@db_operation
async def replace_competition_allocations(
    conn: DatabaseConnection,
    *,
    target: CompetitionAllocationUpdateRequest,
    actor: str,
) -> CompetitionAllocationSnapshot:
    requested = {allocation.set_id: allocation.raw_emission_weight for allocation in target.allocations}
    async with conn.conn.transaction():
        rows = await conn.fetch(
            f"""
            {_ADMIN_COMPETITION_SELECT}
            ORDER BY set_id
            FOR UPDATE
            """
        )
        current = {int(row["set_id"]): row["raw_emission_weight"] for row in rows}
        if requested.keys() != current.keys():
            missing = sorted(current.keys() - requested.keys())
            unknown = sorted(requested.keys() - current.keys())
            raise CompetitionAdminConflictError(
                f"Allocation vector must match all competitions; missing={missing}, unknown={unknown}"
            )

        before = _allocation_snapshot(current)
        if requested == current:
            return before

        await conn.executemany(
            "UPDATE competitions SET raw_emission_weight = $2 WHERE set_id = $1",
            sorted(requested.items()),
        )
        after = _allocation_snapshot(requested)
        await _insert_competition_admin_event(
            conn,
            operation="allocation",
            actor=actor,
            reason=target.reason,
            before_state=_audit_snapshot(before),
            after_state=_audit_snapshot(after),
        )
        return after


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
