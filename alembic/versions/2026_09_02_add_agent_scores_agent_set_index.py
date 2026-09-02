"""Add composite index on agent_scores(agent_id, set_id)

Revision ID: a9c4e1f7b302
Revises: 06f4bede4ef6
Create Date: 2026-09-02 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9c4e1f7b302"
down_revision: Union[str, None] = "06f4bede4ef6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_comp_score_test
            ON agent_scores(agent_id, set_id);
        """)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("""
            DROP INDEX CONCURRENTLY IF EXISTS idx_comp_score_test;
        """)
