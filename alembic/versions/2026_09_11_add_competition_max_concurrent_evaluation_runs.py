"""add competition max_concurrent_evaluation_runs

Revision ID: b18d4c7f2e93
Revises: 4a2c7b91de05
Create Date: 2026-09-11 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "b18d4c7f2e93"
down_revision: Union[str, Sequence[str], None] = "4a2c7b91de05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_POLICY_CONSTRAINT = "ck_competitions_policy_complete"

# Matches the validator-side cap this column replaces, so competitions that already
# carry a policy keep their current concurrency after the backfill.
_DEFAULT_MAX_CONCURRENT_EVALUATION_RUNS = 15

_POLICY_COLUMNS_BEFORE = (
    "scoring_mode, screener_1_threshold, screener_2_threshold, "
    "prune_threshold, required_validator_count, pre_screening_enabled, "
    "auto_approval_enabled, hardcoding_policy_version, incentive_enabled, "
    "incentive_performance_threshold, incentive_cost_threshold, "
    "incentive_reward_half_life_hours, incentive_time_multiplier_scale_hours"
)

_POLICY_COLUMNS_AFTER = (
    "scoring_mode, screener_1_threshold, screener_2_threshold, "
    "prune_threshold, required_validator_count, max_concurrent_evaluation_runs, "
    "pre_screening_enabled, "
    "auto_approval_enabled, hardcoding_policy_version, incentive_enabled, "
    "incentive_performance_threshold, incentive_cost_threshold, "
    "incentive_reward_half_life_hours, incentive_time_multiplier_scale_hours"
)


def upgrade() -> None:
    op.add_column("competitions", sa.Column("max_concurrent_evaluation_runs", sa.Integer(), nullable=True))

    # Competitions that already have a complete policy must gain the new column before
    # the completeness constraint is widened, or every one of them would violate it.
    op.execute(
        f"""
        UPDATE competitions
        SET max_concurrent_evaluation_runs = {_DEFAULT_MAX_CONCURRENT_EVALUATION_RUNS}
        WHERE scoring_mode IS NOT NULL
        """
    )

    op.create_check_constraint(
        "ck_competitions_max_concurrent_evaluation_runs",
        "competitions",
        "max_concurrent_evaluation_runs IS NULL OR max_concurrent_evaluation_runs > 0",
    )

    op.drop_constraint(_POLICY_CONSTRAINT, "competitions", type_="check")
    op.create_check_constraint(
        _POLICY_CONSTRAINT,
        "competitions",
        f"num_nonnulls({_POLICY_COLUMNS_AFTER}) IN (0, 14)",
    )


def downgrade() -> None:
    op.drop_constraint(_POLICY_CONSTRAINT, "competitions", type_="check")
    op.create_check_constraint(
        _POLICY_CONSTRAINT,
        "competitions",
        f"num_nonnulls({_POLICY_COLUMNS_BEFORE}) IN (0, 13)",
    )

    op.drop_constraint("ck_competitions_max_concurrent_evaluation_runs", "competitions", type_="check")
    op.drop_column("competitions", "max_concurrent_evaluation_runs")
