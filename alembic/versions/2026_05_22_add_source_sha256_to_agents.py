"""Add source_sha256 to agents

Revision ID: c4d2e7a9f158
Revises: b3f91c0d4e62
Create Date: 2026-05-22 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c4d2e7a9f158"
down_revision: Union[str, Sequence[str], None] = "b3f91c0d4e62"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("source_sha256", sa.Text(), nullable=True))
    op.create_index("idx_agents_source_sha256", "agents", ["source_sha256"])


def downgrade() -> None:
    op.drop_index("idx_agents_source_sha256", table_name="agents")
    op.drop_column("agents", "source_sha256")
