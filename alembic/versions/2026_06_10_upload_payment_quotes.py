"""Add upload payment quotes

Revision ID: f0a1b2c3d4e5
Revises: c4d9a2e7f813
Create Date: 2026-06-10 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "f0a1b2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "c4d9a2e7f813"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "upload_payment_quotes",
        sa.Column("quote_id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("miner_hotkey", sa.Text(), nullable=False),
        sa.Column("amount_rao", sa.BigInteger(), nullable=False),
        sa.Column("send_address", sa.Text(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("quote_id"),
    )
    op.create_index(
        "idx_upload_payment_quotes_miner_hotkey_created_at",
        "upload_payment_quotes",
        ["miner_hotkey", "created_at"],
    )

    op.add_column("evaluation_payments", sa.Column("quote_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_evaluation_payments_quote_id",
        "evaluation_payments",
        "upload_payment_quotes",
        ["quote_id"],
        ["quote_id"],
    )
    op.create_unique_constraint("uq_evaluation_payments_quote_id", "evaluation_payments", ["quote_id"])


def downgrade() -> None:
    op.drop_constraint("uq_evaluation_payments_quote_id", "evaluation_payments", type_="unique")
    op.drop_constraint("fk_evaluation_payments_quote_id", "evaluation_payments", type_="foreignkey")
    op.drop_column("evaluation_payments", "quote_id")
    op.drop_index("idx_upload_payment_quotes_miner_hotkey_created_at", table_name="upload_payment_quotes")
    op.drop_table("upload_payment_quotes")
