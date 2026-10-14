"""validator scheduling controls

Revision ID: 2d3d67111e36
Revises: a4d2c8e17f55
Create Date: 2026-10-09 02:17:13.276775

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2d3d67111e36"
down_revision: Union[str, Sequence[str], None] = "a4d2c8e17f55"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_PREVIOUS_OPERATIONS = "'state', 'policy', 'allocation', 'metadata', 'validator_concurrency'"


def upgrade() -> None:
    op.add_column(
        "competitions", sa.Column("validator_scheduling_mode", sa.Text(), nullable=False, server_default="normal")
    )
    op.create_check_constraint(
        "ck_competitions_validator_scheduling_mode",
        "competitions",
        "validator_scheduling_mode IN ('disabled', 'normal', 'prioritized')",
    )
    op.create_table(
        "validator_competition_allowlists",
        sa.Column("validator_hotkey", sa.Text(), primary_key=True),
    )
    op.create_table(
        "validator_competition_allowlist_entries",
        sa.Column("validator_hotkey", sa.Text(), primary_key=True),
        sa.Column("set_id", sa.Integer(), primary_key=True),
        sa.ForeignKeyConstraint(
            ["validator_hotkey"], ["validator_competition_allowlists.validator_hotkey"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["set_id"], ["competitions.set_id"]),
    )
    op.create_table(
        "validator_competition_last_served",
        sa.Column("set_id", sa.Integer(), primary_key=True),
        sa.Column("last_served_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["set_id"], ["competitions.set_id"], ondelete="CASCADE"),
    )
    op.drop_constraint("ck_competition_admin_events_operation", "competition_admin_events", type_="check")
    op.create_check_constraint(
        "ck_competition_admin_events_operation",
        "competition_admin_events",
        f"operation IN ({_PREVIOUS_OPERATIONS}, 'validator_scheduling', 'validator_allowlist')",
    )


def downgrade() -> None:
    # Match previous admin-control migrations: these events cannot fit the old
    # operation constraint. Export them before an operator-requested downgrade.
    op.execute(
        "DELETE FROM competition_admin_events WHERE operation IN ('validator_scheduling', 'validator_allowlist')"
    )
    op.drop_constraint("ck_competition_admin_events_operation", "competition_admin_events", type_="check")
    op.create_check_constraint(
        "ck_competition_admin_events_operation",
        "competition_admin_events",
        f"operation IN ({_PREVIOUS_OPERATIONS})",
    )
    op.drop_table("validator_competition_last_served")
    op.drop_table("validator_competition_allowlist_entries")
    op.drop_table("validator_competition_allowlists")
    op.drop_constraint("ck_competitions_validator_scheduling_mode", "competitions", type_="check")
    op.drop_column("competitions", "validator_scheduling_mode")
