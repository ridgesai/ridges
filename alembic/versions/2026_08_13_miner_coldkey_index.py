"""Index agents.miner_coldkey for the miner-console coldkey lookup.

The agents-by-coldkey retrieval endpoint filters on miner_coldkey, which has
no index. (Adapted from the stashed 2026-07-24 miner-console migration; the
payments/refunds indexes it also carried serve the out-of-scope
unredeemed-payments endpoint and are deliberately not included.)

Revision ID: 622a36d5146f
Revises: 550060c2f8a7
Create Date: 2026-08-13

"""

from typing import Sequence, Union

from alembic import op

revision: str = "622a36d5146f"
down_revision: Union[str, None] = "550060c2f8a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_agents_miner_coldkey ON agents (miner_coldkey);")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_agents_miner_coldkey;")
