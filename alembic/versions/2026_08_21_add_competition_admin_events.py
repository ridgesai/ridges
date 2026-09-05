"""add competition admin events

Revision ID: 6c63d9349cfc
Revises: 07af81b81a3e
Create Date: 2026-08-21 03:43:51.873947
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "6c63d9349cfc"
down_revision: Union[str, Sequence[str], None] = "07af81b81a3e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "competition_admin_events",
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("before_state", postgresql.JSONB(), nullable=False),
        sa.Column("after_state", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
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
        sa.PrimaryKeyConstraint("event_id"),
    )


def downgrade() -> None:
    op.drop_table("competition_admin_events")
