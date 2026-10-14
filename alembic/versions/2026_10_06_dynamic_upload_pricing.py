from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7c3e9a1f4b20"
down_revision: Union[str, Sequence[str], None] = "39daf859ec77"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PRICE_COLUMNS = ("floor_usd", "target_per_hour", "half_life_minutes", "price_usd")
_POSITIVE = " AND ".join(
    f"{column} > 0 AND {column} NOT IN ('NaN'::numeric, 'Infinity'::numeric)" for column in _PRICE_COLUMNS
)


def upgrade() -> None:
    op.create_table(
        "competition_upload_prices",
        sa.Column(
            "set_id",
            sa.Integer(),
            sa.ForeignKey("competitions.set_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("floor_usd", sa.Numeric(), nullable=False, server_default=sa.text("5")),
        sa.Column("target_per_hour", sa.Numeric(), nullable=False, server_default=sa.text("10")),
        sa.Column("half_life_minutes", sa.Numeric(), nullable=False, server_default=sa.text("30")),
        sa.Column("price_usd", sa.Numeric(), nullable=False),
        sa.Column("price_updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint(_POSITIVE, name="ck_competition_upload_prices_positive"),
        sa.CheckConstraint(
            "target_per_hour * half_life_minutes / 60 >= 1.71",
            name="ck_competition_upload_prices_max_multiplier",
        ),
    )

    op.add_column("upload_payment_quotes", sa.Column("set_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_upload_payment_quotes_set_id",
        "upload_payment_quotes",
        "competitions",
        ["set_id"],
        ["set_id"],
    )
    op.add_column("upload_payment_quotes", sa.Column("price_usd", sa.Numeric(), nullable=True))
    op.add_column("upload_payment_quotes", sa.Column("miner_coldkey", sa.Text(), nullable=True))
    op.add_column("upload_payment_quotes", sa.Column("confirmed_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column("upload_payment_quotes", sa.Column("confirmed_payment_block_hash", sa.Text(), nullable=True))
    op.add_column("upload_payment_quotes", sa.Column("confirmed_payment_extrinsic_index", sa.Text(), nullable=True))
    op.add_column("upload_payment_quotes", sa.Column("cancelled_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column(
        "upload_payment_quotes",
        sa.Column("is_legacy", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    # Every quote that exists now predates competition binding and keeps today's behaviour.
    op.execute("UPDATE upload_payment_quotes SET is_legacy = true")

    op.create_check_constraint(
        "ck_upload_payment_quotes_bound",
        "upload_payment_quotes",
        "is_legacy OR (set_id IS NOT NULL AND price_usd IS NOT NULL AND miner_coldkey IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_upload_payment_quotes_terminal",
        "upload_payment_quotes",
        "NOT (confirmed_at IS NOT NULL AND cancelled_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_upload_payment_quotes_confirmed_receipt",
        "upload_payment_quotes",
        "(confirmed_at IS NULL) = (confirmed_payment_block_hash IS NULL) "
        "AND (confirmed_at IS NULL) = (confirmed_payment_extrinsic_index IS NULL)",
    )


def downgrade() -> None:
    bound = op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM upload_payment_quotes WHERE NOT is_legacy"))
    if bound:
        raise RuntimeError(
            "Cannot downgrade while competition-bound quotes exist: they would become unrestricted legacy tickets."
        )

    for name in (
        "ck_upload_payment_quotes_confirmed_receipt",
        "ck_upload_payment_quotes_terminal",
        "ck_upload_payment_quotes_bound",
    ):
        op.drop_constraint(name, "upload_payment_quotes", type_="check")
    op.drop_constraint("fk_upload_payment_quotes_set_id", "upload_payment_quotes", type_="foreignkey")
    for column in (
        "is_legacy",
        "cancelled_at",
        "confirmed_payment_extrinsic_index",
        "confirmed_payment_block_hash",
        "confirmed_at",
        "miner_coldkey",
        "price_usd",
        "set_id",
    ):
        op.drop_column("upload_payment_quotes", column)
    op.drop_table("competition_upload_prices")
