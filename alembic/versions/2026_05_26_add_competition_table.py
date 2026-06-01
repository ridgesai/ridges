"""Add competition table

Revision ID: b1d5e8a3f902
Revises: f2a8b1d7e904
Create Date: 2026-05-26 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "b1d5e8a3f902"
down_revision: Union[str, Sequence[str], None] = "f2a8b1d7e904"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "competitions",
        sa.Column("set_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("start_date", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("end_date", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("set_id"),
    )


def downgrade() -> None:
    op.drop_table("competitions")
