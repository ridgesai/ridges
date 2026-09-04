from datetime import datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, CreatedAtMixin


class Competition(Base, CreatedAtMixin):
    __tablename__ = "competitions"

    set_id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    name: Mapped[Optional[str]] = mapped_column(sa.Text)
    description: Mapped[Optional[str]] = mapped_column(sa.Text)
    links: Mapped[list[str]] = mapped_column(
        ARRAY(sa.Text),
        nullable=False,
        server_default=sa.text("'{}'::text[]"),
    )
    start_date: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True))
    submissions_closed_at: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True))
    is_paused: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.text("false"))
    emissions_end_at: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True))
    end_date: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True))
    raw_emission_weight: Mapped[Decimal] = mapped_column(
        sa.Numeric(),
        nullable=False,
        server_default=sa.text("0"),
    )
    scoring_mode: Mapped[Optional[str]] = mapped_column(sa.Text)
    screener_1_threshold: Mapped[Optional[Decimal]] = mapped_column(sa.Numeric())
    screener_2_threshold: Mapped[Optional[Decimal]] = mapped_column(sa.Numeric())
    prune_threshold: Mapped[Optional[Decimal]] = mapped_column(sa.Numeric())
    required_validator_count: Mapped[Optional[int]] = mapped_column(sa.Integer)
    pre_screening_enabled: Mapped[Optional[bool]] = mapped_column(sa.Boolean)
    auto_approval_enabled: Mapped[Optional[bool]] = mapped_column(sa.Boolean)
    hardcoding_policy_version: Mapped[Optional[str]] = mapped_column(sa.Text)
    incentive_enabled: Mapped[Optional[bool]] = mapped_column(sa.Boolean)
    incentive_performance_threshold: Mapped[Optional[Decimal]] = mapped_column(sa.Numeric())
    incentive_cost_threshold: Mapped[Optional[Decimal]] = mapped_column(sa.Numeric())
    incentive_reward_half_life_hours: Mapped[Optional[Decimal]] = mapped_column(sa.Numeric())
    incentive_time_multiplier_scale_hours: Mapped[Optional[Decimal]] = mapped_column(sa.Numeric())

    __table_args__ = (
        sa.CheckConstraint(
            "raw_emission_weight BETWEEN 0 AND 1 "
            "AND raw_emission_weight NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
            name="ck_competitions_raw_emission_weight_finite_range",
        ),
        sa.CheckConstraint(
            "num_nonnulls(scoring_mode, screener_1_threshold, screener_2_threshold, "
            "prune_threshold, required_validator_count, pre_screening_enabled, "
            "auto_approval_enabled, hardcoding_policy_version, incentive_enabled, "
            "incentive_performance_threshold, incentive_cost_threshold, "
            "incentive_reward_half_life_hours, incentive_time_multiplier_scale_hours) IN (0, 13)",
            name="ck_competitions_policy_complete",
        ),
        sa.CheckConstraint(
            "scoring_mode IS NULL OR scoring_mode IN ('legacy', 'consensus')",
            name="ck_competitions_scoring_mode",
        ),
        sa.CheckConstraint(
            "(screener_1_threshold IS NULL OR (screener_1_threshold BETWEEN 0 AND 1 "
            "AND screener_1_threshold NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric))) "
            "AND (screener_2_threshold IS NULL OR (screener_2_threshold BETWEEN 0 AND 1 "
            "AND screener_2_threshold NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric))) "
            "AND (prune_threshold IS NULL OR (prune_threshold BETWEEN 0 AND 1 "
            "AND prune_threshold NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)))",
            name="ck_competitions_screening_thresholds",
        ),
        sa.CheckConstraint(
            "required_validator_count IS NULL OR required_validator_count > 0",
            name="ck_competitions_required_validator_count",
        ),
        sa.CheckConstraint(
            "hardcoding_policy_version IS NULL OR length(btrim(hardcoding_policy_version)) > 0",
            name="ck_competitions_hardcoding_policy_version",
        ),
        sa.CheckConstraint(
            "(incentive_performance_threshold IS NULL OR (incentive_performance_threshold > 0 "
            "AND incentive_performance_threshold < 1 "
            "AND incentive_performance_threshold "
            "NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric))) "
            "AND (incentive_cost_threshold IS NULL OR (incentive_cost_threshold > 0 "
            "AND incentive_cost_threshold < 1 "
            "AND incentive_cost_threshold "
            "NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)))",
            name="ck_competitions_incentive_thresholds",
        ),
        sa.CheckConstraint(
            "(incentive_reward_half_life_hours IS NULL OR (incentive_reward_half_life_hours > 0 "
            "AND incentive_reward_half_life_hours "
            "NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric))) "
            "AND (incentive_time_multiplier_scale_hours IS NULL "
            "OR (incentive_time_multiplier_scale_hours > 0 "
            "AND incentive_time_multiplier_scale_hours "
            "NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)))",
            name="ck_competitions_incentive_durations",
        ),
        sa.CheckConstraint(
            "coalesce(competitions_links_are_nonblank(links), true)",
            name="ck_competitions_links_nonblank",
        ),
        sa.CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR end_date >= start_date",
            name="ck_competitions_end_not_before_start",
        ),
        sa.CheckConstraint(
            "(submissions_closed_at IS NULL AND emissions_end_at IS NULL) "
            "OR (submissions_closed_at IS NOT NULL AND emissions_end_at IS NOT NULL "
            "AND emissions_end_at >= submissions_closed_at)",
            name="ck_competitions_submission_emissions_window",
        ),
    )


class CompetitionAdminEvent(Base, CreatedAtMixin):
    __tablename__ = "competition_admin_events"

    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )
    operation: Mapped[str] = mapped_column(sa.Text, nullable=False)
    actor: Mapped[str] = mapped_column(sa.Text, nullable=False)
    reason: Mapped[str] = mapped_column(sa.Text, nullable=False)
    before_state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    after_state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        sa.CheckConstraint(
            "operation IN ('state', 'policy', 'allocation')",
            name="ck_competition_admin_events_operation",
        ),
        sa.CheckConstraint(
            "length(btrim(actor)) > 0",
            name="ck_competition_admin_events_actor_nonblank",
        ),
        sa.CheckConstraint(
            "length(btrim(reason)) > 0",
            name="ck_competition_admin_events_reason_nonblank",
        ),
    )


class CompetitionWorkCursor(Base):
    __tablename__ = "competition_work_cursors"

    family: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    last_served_set_id: Mapped[Optional[int]] = mapped_column(sa.Integer)

    __table_args__ = (
        sa.CheckConstraint(
            "family IN ('screener_1', 'screener_2', 'validator', 'pre_screening_judge', 'approval_judge')",
            name="ck_competition_work_cursors_family",
        ),
    )
