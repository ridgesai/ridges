"""Add score-bound pruning indexes

Revision ID: f4e8b7c2d901
Revises: c880b27e2afe
Create Date: 2026-05-14 17:30:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "f4e8b7c2d901"
down_revision: Union[str, Sequence[str], None] = "c880b27e2afe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_scores_pruning_leader
        ON agent_scores (set_id, validator_count, final_score DESC)
        WHERE approved IS TRUE;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_agent_scores_pruning_leader;")
