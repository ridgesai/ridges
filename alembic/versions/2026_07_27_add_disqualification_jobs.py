"""Add disqualification_jobs table.

Revision ID: a1b2c3d4e5f6
Revises: e5c8a1f0b942
Create Date: 2026-07-27 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "e5c8a1f0b942"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "disqualification_jobs",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "agent_id",
            sa.UUID(),
            sa.ForeignKey("agents.agent_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("set_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index(
        "uq_disqualification_jobs_pending",
        "disqualification_jobs",
        ["agent_id"],
        unique=True,
        postgresql_where=sa.text("processed_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_disqualification_jobs_pending", table_name="disqualification_jobs")
    op.drop_table("disqualification_jobs")
