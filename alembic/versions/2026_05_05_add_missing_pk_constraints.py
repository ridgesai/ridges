"""add missing pk constraints to banned_hotkeys and unapproved_agent_ids

Revision ID: e3f8c1d4a2b9
Revises: b8d3f6e2c9a5
Create Date: 2026-05-05 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "e3f8c1d4a2b9"
down_revision: Union[str, None] = "b8d3f6e2c9a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Remove duplicates before adding PK constraints
    op.execute("""
        DELETE FROM banned_hotkeys
        WHERE ctid NOT IN (
            SELECT MIN(ctid)
            FROM banned_hotkeys
            GROUP BY miner_hotkey
        )
    """)
    op.execute("""
        DELETE FROM unapproved_agent_ids
        WHERE ctid NOT IN (
            SELECT MIN(ctid)
            FROM unapproved_agent_ids
            GROUP BY agent_id
        )
    """)

    op.drop_index("idx_unapproved_agent_ids_agent_id", table_name="unapproved_agent_ids")
    op.create_primary_key("unapproved_agent_ids_pkey", "unapproved_agent_ids", ["agent_id"])

    op.create_primary_key("banned_hotkeys_pkey", "banned_hotkeys", ["miner_hotkey"])


def downgrade() -> None:
    op.execute("ALTER TABLE banned_hotkeys DROP CONSTRAINT banned_hotkeys_pkey")

    op.execute("ALTER TABLE unapproved_agent_ids DROP CONSTRAINT unapproved_agent_ids_pkey")
    op.create_index(
        "idx_unapproved_agent_ids_agent_id",
        "unapproved_agent_ids",
        ["agent_id"],
        unique=True,
    )
