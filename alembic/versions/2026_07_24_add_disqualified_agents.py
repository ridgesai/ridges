"""Add disqualified_agents table and disqualified_agent_ids view.

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


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS disqualified_agent_ids")
    op.drop_table("disqualified_agents")
