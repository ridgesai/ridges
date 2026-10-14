from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4d2c8e17f55"
down_revision: Union[str, Sequence[str], None] = "7c3e9a1f4b20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A purchased quote is a prepaid upload: it records the USD price at purchase, the alpha debited for it,
    # and the agent it was redeemed for.
    op.add_column("upload_payment_quotes", sa.Column("purchased_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column("upload_payment_quotes", sa.Column("purchase_price_usd", sa.Numeric(), nullable=True))
    op.add_column("upload_payment_quotes", sa.Column("purchase_price_alpha_rao", sa.BigInteger(), nullable=True))
    op.add_column(
        "upload_payment_quotes",
        sa.Column("redeemed_agent_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_upload_payment_quotes_purchase",
        "upload_payment_quotes",
        "(purchased_at IS NULL) = (purchase_price_usd IS NULL) "
        "AND (purchased_at IS NULL) = (purchase_price_alpha_rao IS NULL) "
        "AND (redeemed_agent_id IS NULL OR purchased_at IS NOT NULL) "
        "AND NOT (purchased_at IS NOT NULL AND cancelled_at IS NOT NULL)",
    )

    # Per-coldkey burn balance, in rao: burns credit, purchases debit.
    op.create_table(
        "burn_balance_entries",
        sa.Column(
            "entry_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("miner_coldkey", sa.Text(), nullable=False),
        sa.Column(
            "quote_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("upload_payment_quotes.quote_id"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("amount_alpha_rao", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("quote_id", "kind", name="uq_burn_balance_entries_quote_kind"),
        sa.CheckConstraint("kind IN ('burn', 'purchase')", name="ck_burn_balance_entries_kind"),
        sa.CheckConstraint(
            "(kind = 'purchase' AND amount_alpha_rao < 0) OR (kind <> 'purchase' AND amount_alpha_rao > 0)",
            name="ck_burn_balance_entries_sign",
        ),
    )
    op.create_index("idx_burn_balance_entries_coldkey", "burn_balance_entries", ["miner_coldkey"])


def downgrade() -> None:
    op.drop_index("idx_burn_balance_entries_coldkey", table_name="burn_balance_entries")
    op.drop_table("burn_balance_entries")
    op.drop_constraint("ck_upload_payment_quotes_purchase", "upload_payment_quotes", type_="check")
    for column in (
        "redeemed_agent_id",
        "purchase_price_alpha_rao",
        "purchase_price_usd",
        "purchased_at",
    ):
        op.drop_column("upload_payment_quotes", column)
