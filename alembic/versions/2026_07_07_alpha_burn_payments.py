"""Add alpha burn payment columns

Revision ID: a1b2c3d4e5f6
Revises: e7f3a1b2c905
Create Date: 2026-07-07 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "e7f3a1b2c905"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("evaluation_payments", sa.Column("amount_alpha_rao", sa.BigInteger(), nullable=True))
    op.add_column("upload_payment_quotes", sa.Column("amount_alpha_rao", sa.BigInteger(), nullable=True))
    op.alter_column("evaluation_payments", "amount_rao", existing_type=sa.Integer(), nullable=True)
    op.alter_column("upload_payment_quotes", "amount_rao", existing_type=sa.BigInteger(), nullable=True)
    op.alter_column("upload_payment_quotes", "send_address", existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    op.alter_column("upload_payment_quotes", "send_address", existing_type=sa.Text(), nullable=False)
    op.alter_column("upload_payment_quotes", "amount_rao", existing_type=sa.BigInteger(), nullable=False)
    op.alter_column("evaluation_payments", "amount_rao", existing_type=sa.Integer(), nullable=False)
    op.drop_column("upload_payment_quotes", "amount_alpha_rao")
    op.drop_column("evaluation_payments", "amount_alpha_rao")
