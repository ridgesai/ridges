"""Add top-k incentive snapshots to approved agents.

Revision ID: d2a7f4c9e318
Revises: b7e4d2c9a106
Create Date: 2026-07-14 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "d2a7f4c9e318"
down_revision: Union[str, Sequence[str], None] = "b7e4d2c9a106"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "approved_agents",
        sa.Column(
            "baseline_agent_id",
            sa.UUID(),
            sa.ForeignKey("agents.agent_id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("approved_agents", sa.Column("performance_delta", sa.Float(), nullable=True))
    op.add_column("approved_agents", sa.Column("cost_delta", sa.Float(), nullable=True))
    op.add_column("approved_agents", sa.Column("raw_improvement", sa.Float(), nullable=True))
    op.add_column("approved_agents", sa.Column("time_multiplier", sa.Float(), nullable=True))
    op.add_column("approved_agents", sa.Column("initial_improvement_bonus", sa.Float(), nullable=True))
    op.create_index(
        "idx_approved_agents_set_approved_at",
        "approved_agents",
        ["set_id", sa.text("approved_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_approved_agents_set_approved_at", table_name="approved_agents")
    op.drop_column("approved_agents", "initial_improvement_bonus")
    op.drop_column("approved_agents", "time_multiplier")
    op.drop_column("approved_agents", "raw_improvement")
    op.drop_column("approved_agents", "cost_delta")
    op.drop_column("approved_agents", "performance_delta")
    op.drop_column("approved_agents", "baseline_agent_id")
