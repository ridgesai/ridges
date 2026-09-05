"""Add the multi-competition lifecycle and policy foundation.

Revision ID: 6f59f4e0c487
Revises: 622a36d5146f
Create Date: 2026-08-20

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "6f59f4e0c487"
down_revision: Union[str, None] = "622a36d5146f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "competitions",
        sa.Column("submissions_closed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "competitions",
        sa.Column("is_paused", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "competitions",
        sa.Column("emissions_end_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "competitions",
        sa.Column("raw_emission_weight", sa.Numeric(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "competitions",
        sa.Column("scoring_mode", sa.Text(), nullable=True),
    )
    op.add_column(
        "competitions",
        sa.Column("screener_1_threshold", sa.Numeric(), nullable=True),
    )
    op.add_column(
        "competitions",
        sa.Column("screener_2_threshold", sa.Numeric(), nullable=True),
    )
    op.add_column(
        "competitions",
        sa.Column("prune_threshold", sa.Numeric(), nullable=True),
    )
    op.add_column(
        "competitions",
        sa.Column("required_validator_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "competitions",
        sa.Column("pre_screening_enabled", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "competitions",
        sa.Column("auto_approval_enabled", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "competitions",
        sa.Column("hardcoding_policy_version", sa.Text(), nullable=True),
    )
    op.add_column(
        "competitions",
        sa.Column("incentive_enabled", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "competitions",
        sa.Column("incentive_performance_threshold", sa.Numeric(), nullable=True),
    )
    op.add_column(
        "competitions",
        sa.Column("incentive_cost_threshold", sa.Numeric(), nullable=True),
    )
    op.add_column(
        "competitions",
        sa.Column("incentive_reward_half_life_hours", sa.Numeric(), nullable=True),
    )
    op.add_column(
        "competitions",
        sa.Column("incentive_time_multiplier_scale_hours", sa.Numeric(), nullable=True),
    )
    op.execute("""
        UPDATE competitions
        SET raw_emission_weight = 1
        WHERE set_id = (
            SELECT MAX(set_id)
            FROM competitions
            WHERE end_date IS NULL
        )
    """)

    op.create_check_constraint(
        "ck_competitions_raw_emission_weight_finite_range",
        "competitions",
        "raw_emission_weight BETWEEN 0 AND 1 "
        "AND raw_emission_weight NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)",
    )
    op.create_check_constraint(
        "ck_competitions_policy_complete",
        "competitions",
        "num_nonnulls(scoring_mode, screener_1_threshold, screener_2_threshold, "
        "prune_threshold, required_validator_count, pre_screening_enabled, "
        "auto_approval_enabled, hardcoding_policy_version, incentive_enabled, "
        "incentive_performance_threshold, incentive_cost_threshold, "
        "incentive_reward_half_life_hours, incentive_time_multiplier_scale_hours) IN (0, 13)",
    )
    op.create_check_constraint(
        "ck_competitions_scoring_mode",
        "competitions",
        "scoring_mode IS NULL OR scoring_mode IN ('legacy', 'consensus')",
    )
    op.create_check_constraint(
        "ck_competitions_screening_thresholds",
        "competitions",
        "(screener_1_threshold IS NULL OR (screener_1_threshold BETWEEN 0 AND 1 "
        "AND screener_1_threshold NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric))) "
        "AND (screener_2_threshold IS NULL OR (screener_2_threshold BETWEEN 0 AND 1 "
        "AND screener_2_threshold NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric))) "
        "AND (prune_threshold IS NULL OR (prune_threshold BETWEEN 0 AND 1 "
        "AND prune_threshold NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)))",
    )
    op.create_check_constraint(
        "ck_competitions_required_validator_count",
        "competitions",
        "required_validator_count IS NULL OR required_validator_count > 0",
    )
    op.create_check_constraint(
        "ck_competitions_hardcoding_policy_version",
        "competitions",
        "hardcoding_policy_version IS NULL OR length(btrim(hardcoding_policy_version)) > 0",
    )
    op.create_check_constraint(
        "ck_competitions_incentive_thresholds",
        "competitions",
        "(incentive_performance_threshold IS NULL OR (incentive_performance_threshold > 0 "
        "AND incentive_performance_threshold < 1 "
        "AND incentive_performance_threshold "
        "NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric))) "
        "AND (incentive_cost_threshold IS NULL OR (incentive_cost_threshold > 0 "
        "AND incentive_cost_threshold < 1 "
        "AND incentive_cost_threshold "
        "NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)))",
    )
    op.create_check_constraint(
        "ck_competitions_incentive_durations",
        "competitions",
        "(incentive_reward_half_life_hours IS NULL OR (incentive_reward_half_life_hours > 0 "
        "AND incentive_reward_half_life_hours "
        "NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric))) "
        "AND (incentive_time_multiplier_scale_hours IS NULL "
        "OR (incentive_time_multiplier_scale_hours > 0 "
        "AND incentive_time_multiplier_scale_hours "
        "NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)))",
    )
    op.create_check_constraint(
        "ck_competitions_end_not_before_start",
        "competitions",
        "end_date IS NULL OR start_date IS NULL OR end_date >= start_date",
    )
    op.create_check_constraint(
        "ck_competitions_submission_emissions_window",
        "competitions",
        "(submissions_closed_at IS NULL AND emissions_end_at IS NULL) "
        "OR (submissions_closed_at IS NOT NULL AND emissions_end_at IS NOT NULL "
        "AND emissions_end_at >= submissions_closed_at)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_competitions_submission_emissions_window",
        "competitions",
        type_="check",
    )
    op.drop_constraint(
        "ck_competitions_end_not_before_start",
        "competitions",
        type_="check",
    )
    op.drop_constraint(
        "ck_competitions_incentive_durations",
        "competitions",
        type_="check",
    )
    op.drop_constraint(
        "ck_competitions_incentive_thresholds",
        "competitions",
        type_="check",
    )
    op.drop_constraint(
        "ck_competitions_hardcoding_policy_version",
        "competitions",
        type_="check",
    )
    op.drop_constraint(
        "ck_competitions_required_validator_count",
        "competitions",
        type_="check",
    )
    op.drop_constraint(
        "ck_competitions_screening_thresholds",
        "competitions",
        type_="check",
    )
    op.drop_constraint(
        "ck_competitions_scoring_mode",
        "competitions",
        type_="check",
    )
    op.drop_constraint(
        "ck_competitions_policy_complete",
        "competitions",
        type_="check",
    )
    op.drop_constraint(
        "ck_competitions_raw_emission_weight_finite_range",
        "competitions",
        type_="check",
    )
    op.drop_column("competitions", "incentive_time_multiplier_scale_hours")
    op.drop_column("competitions", "incentive_reward_half_life_hours")
    op.drop_column("competitions", "incentive_cost_threshold")
    op.drop_column("competitions", "incentive_performance_threshold")
    op.drop_column("competitions", "incentive_enabled")
    op.drop_column("competitions", "hardcoding_policy_version")
    op.drop_column("competitions", "auto_approval_enabled")
    op.drop_column("competitions", "pre_screening_enabled")
    op.drop_column("competitions", "required_validator_count")
    op.drop_column("competitions", "prune_threshold")
    op.drop_column("competitions", "screener_2_threshold")
    op.drop_column("competitions", "screener_1_threshold")
    op.drop_column("competitions", "scoring_mode")
    op.drop_column("competitions", "raw_emission_weight")
    op.drop_column("competitions", "emissions_end_at")
    op.drop_column("competitions", "is_paused")
    op.drop_column("competitions", "submissions_closed_at")
