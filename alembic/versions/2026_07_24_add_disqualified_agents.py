"""Add disqualified_agents table, disqualified_agent_ids view, and disqualification_jobs table.

Revision ID: e5c8a1f0b942
Revises: b3f1a9c4d210
Create Date: 2026-07-24 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "e5c8a1f0b942"
down_revision: Union[str, Sequence[str], None] = "b3f1a9c4d210"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CREATE_VIEW = """
CREATE VIEW disqualified_agent_ids AS
    SELECT a.agent_id
    FROM agents a
    JOIN banned_coldkeys bc ON bc.miner_coldkey = a.miner_coldkey
  UNION
    SELECT agent_id
    FROM disqualified_agents;
"""


def upgrade() -> None:
    op.create_table(
        "disqualified_agents",
        sa.Column(
            "agent_id",
            sa.UUID(),
            sa.ForeignKey("agents.agent_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "disqualified_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
    )
    op.execute(_CREATE_VIEW)

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
    op.execute("DROP VIEW IF EXISTS disqualified_agent_ids")
    op.drop_table("disqualified_agents")
