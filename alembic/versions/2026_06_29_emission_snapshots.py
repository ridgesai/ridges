"""Add emission_snapshots table

Revision ID: f8a9b0c1d2e3
Revises: e7f3a1b2c905
Create Date: 2026-06-29 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "f8a9b0c1d2e3"
down_revision: Union[str, Sequence[str], None] = "e7f3a1b2c905"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "emission_snapshots",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("hotkey", sa.Text, nullable=False),
        sa.Column("agent_id", sa.UUID(), sa.ForeignKey("agents.agent_id"), nullable=False),
        sa.Column("set_id", sa.Integer, sa.ForeignKey("competitions.set_id"), nullable=False),
        sa.Column("block_number", sa.BigInteger, nullable=False),
        sa.Column("emission", sa.Float, nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("hotkey", "block_number", name="uq_emission_snapshots_hotkey_block"),
    )
    op.create_index(
        "idx_emission_snapshots_agent_set",
        "emission_snapshots",
        ["agent_id", "set_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_emission_snapshots_agent_set", table_name="emission_snapshots")
    op.drop_table("emission_snapshots")
