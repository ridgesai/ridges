"""add_payment_extrinsic_index_to_refunds

Revision ID: 1eb5a5a85c77
Revises: b8d3f6e2c9a5
Create Date: 2026-05-06 17:46:21.677349

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1eb5a5a85c77"
down_revision: Union[str, Sequence[str], None] = "b8d3f6e2c9a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Set a default value of '' to backfill existing rows, then remove the default so future inserts must supply the value explicitly
    op.add_column(
        "failed_upload_refunds",
        sa.Column(
            "payment_extrinsic_index",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
    )
    op.alter_column("failed_upload_refunds", "payment_extrinsic_index", server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("failed_upload_refunds", "payment_extrinsic_index")
