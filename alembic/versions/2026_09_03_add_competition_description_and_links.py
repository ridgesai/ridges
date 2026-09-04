"""add competition description, links, and metadata admin events

Revision ID: 4a2c7b91de05
Revises: 06f4bede4ef6
Create Date: 2026-09-03 10:12:44.201883
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "4a2c7b91de05"
down_revision: Union[str, Sequence[str], None] = "06f4bede4ef6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OPERATION_CONSTRAINT = "ck_competition_admin_events_operation"


def upgrade() -> None:
    op.add_column("competitions", sa.Column("description", sa.Text(), nullable=True))
    op.add_column(
        "competitions",
        sa.Column(
            "links",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
    )
    op.execute(
        """
        CREATE FUNCTION competitions_links_are_nonblank(links text[]) RETURNS boolean
        LANGUAGE sql IMMUTABLE AS $$
            SELECT bool_and(link IS NOT NULL AND btrim(link, E' \\t\\n\\r') <> '')
            FROM unnest(links) AS link
        $$
        """
    )
    op.create_check_constraint(
        "ck_competitions_links_nonblank",
        "competitions",
        "coalesce(competitions_links_are_nonblank(links), true)",
    )

    # Editorial updates are audited alongside state, policy, and allocation changes.
    op.drop_constraint(_OPERATION_CONSTRAINT, "competition_admin_events", type_="check")
    op.create_check_constraint(
        _OPERATION_CONSTRAINT,
        "competition_admin_events",
        "operation IN ('state', 'policy', 'allocation', 'metadata')",
    )


def downgrade() -> None:
    # Metadata events cannot satisfy the narrower constraint, so drop them first.
    op.execute("DELETE FROM competition_admin_events WHERE operation = 'metadata'")
    op.drop_constraint(_OPERATION_CONSTRAINT, "competition_admin_events", type_="check")
    op.create_check_constraint(
        _OPERATION_CONSTRAINT,
        "competition_admin_events",
        "operation IN ('state', 'policy', 'allocation')",
    )

    op.drop_constraint("ck_competitions_links_nonblank", "competitions", type_="check")
    op.execute("DROP FUNCTION IF EXISTS competitions_links_are_nonblank(text[])")
    op.drop_column("competitions", "links")
    op.drop_column("competitions", "description")
