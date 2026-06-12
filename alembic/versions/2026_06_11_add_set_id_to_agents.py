"""Add set_id column to agents table

Revision ID: d4cb2c9dc03c
Revises: c4d9a2e7f813
Create Date: 2026-06-11 17:50:23.146144

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "d4cb2c9dc03c"
down_revision: Union[str, Sequence[str], None] = "c4d9a2e7f813"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("set_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_agents_set_id", "agents", "competitions", ["set_id"], ["set_id"])
    op.create_index("idx_agents_set_id", "agents", ["set_id"])


def downgrade() -> None:
    op.drop_index("idx_agents_set_id", table_name="agents")
    op.drop_constraint("fk_agents_set_id", "agents", type_="foreignkey")
    op.drop_column("agents", "set_id")
