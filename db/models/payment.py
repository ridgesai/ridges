from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, CreatedAtMixin


class EvaluationPayment(Base, CreatedAtMixin):
    __tablename__ = "evaluation_payments"

    payment_block_hash: Mapped[str] = mapped_column(sa.Text, nullable=False)
    payment_extrinsic_index: Mapped[str] = mapped_column(sa.Text, nullable=False)
    quote_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("upload_payment_quotes.quote_id"),
        nullable=True,
        comment="Server-issued upload payment quote used to validate amount, destination, hotkey, and payment time.",
    )
    upload_credit_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("upload_credits.credit_id"),
        nullable=True,
        unique=True,
        comment="One-shot upload credit used instead of an on-chain burn.",
    )
    agent_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("agents.agent_id"),
        nullable=True,
        comment="Agent ID associated with this evaluation payment. The payment row is first created with no agent ID to claim an evaluation payment for a specific block hash + extrinsic index.",
    )
    miner_hotkey: Mapped[str] = mapped_column(sa.Text, nullable=False)
    miner_coldkey: Mapped[str] = mapped_column(sa.Text, nullable=False)
    amount_rao: Mapped[Optional[int]] = mapped_column(sa.Integer, nullable=True)
    amount_alpha_rao: Mapped[Optional[int]] = mapped_column(sa.BigInteger, nullable=True)

    __table_args__ = (
        sa.PrimaryKeyConstraint("payment_block_hash", "payment_extrinsic_index"),
        sa.CheckConstraint(
            "num_nonnulls(amount_rao, amount_alpha_rao) = 1",
            name="ck_amount_rao_xor_amount_alpha_rao",
        ),
        sa.CheckConstraint(
            "upload_credit_id IS NULL OR (amount_alpha_rao = 0 AND amount_rao IS NULL AND quote_id IS NULL)",
            name="ck_evaluation_payments_credit_shape",
        ),
    )


class UploadPaymentQuote(Base, CreatedAtMixin):
    __tablename__ = "upload_payment_quotes"

    quote_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )
    miner_hotkey: Mapped[str] = mapped_column(sa.Text, nullable=False)
    amount_rao: Mapped[Optional[int]] = mapped_column(sa.BigInteger, nullable=True)
    amount_alpha_rao: Mapped[Optional[int]] = mapped_column(sa.BigInteger, nullable=True)
    send_address: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=False)
    set_id: Mapped[Optional[int]] = mapped_column(
        sa.Integer, sa.ForeignKey("competitions.set_id", name="fk_upload_payment_quotes_set_id"), nullable=True
    )
    price_usd: Mapped[Optional[Decimal]] = mapped_column(sa.Numeric(), nullable=True)
    miner_coldkey: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=True)
    confirmed_payment_block_hash: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    confirmed_payment_extrinsic_index: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=True)
    is_legacy: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.text("false"))
    purchased_at: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=True)
    purchase_price_usd: Mapped[Optional[Decimal]] = mapped_column(sa.Numeric(), nullable=True)
    purchase_price_alpha_rao: Mapped[Optional[int]] = mapped_column(sa.BigInteger, nullable=True)
    redeemed_agent_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    refunded_at: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=True)

    __table_args__ = (
        sa.CheckConstraint(
            "num_nonnulls(amount_rao, amount_alpha_rao) = 1",
            name="ck_amount_rao_xor_amount_alpha_rao",
        ),
        sa.CheckConstraint(
            "(purchased_at IS NULL) = (purchase_price_usd IS NULL) "
            "AND (purchased_at IS NULL) = (purchase_price_alpha_rao IS NULL) "
            "AND (redeemed_agent_id IS NULL OR purchased_at IS NOT NULL) "
            "AND (refunded_at IS NULL OR (purchased_at IS NOT NULL AND redeemed_agent_id IS NULL)) "
            "AND NOT (purchased_at IS NOT NULL AND cancelled_at IS NOT NULL)",
            name="ck_upload_payment_quotes_purchase",
        ),
        sa.CheckConstraint(
            "is_legacy OR (set_id IS NOT NULL AND price_usd IS NOT NULL AND miner_coldkey IS NOT NULL)",
            name="ck_upload_payment_quotes_bound",
        ),
        sa.CheckConstraint(
            "NOT (confirmed_at IS NOT NULL AND cancelled_at IS NOT NULL)",
            name="ck_upload_payment_quotes_terminal",
        ),
        sa.CheckConstraint(
            "(confirmed_at IS NULL) = (confirmed_payment_block_hash IS NULL) "
            "AND (confirmed_at IS NULL) = (confirmed_payment_extrinsic_index IS NULL)",
            name="ck_upload_payment_quotes_confirmed_receipt",
        ),
        sa.Index(
            "idx_upload_payment_quotes_open",
            "set_id",
            "miner_coldkey",
            "expires_at",
            postgresql_where=sa.text("NOT is_legacy"),
        ),
    )


_PRICE_COLUMNS = ("floor_usd", "target_per_hour", "half_life_minutes", "price_usd")


class CompetitionUploadPrice(Base):
    """The live upload price of one competition, stored as of its last bump, plus its pricing settings."""

    __tablename__ = "competition_upload_prices"

    set_id: Mapped[int] = mapped_column(
        sa.Integer, sa.ForeignKey("competitions.set_id", ondelete="CASCADE"), primary_key=True
    )
    floor_usd: Mapped[Decimal] = mapped_column(sa.Numeric(), nullable=False, server_default=sa.text("5"))
    target_per_hour: Mapped[Decimal] = mapped_column(sa.Numeric(), nullable=False, server_default=sa.text("10"))
    half_life_minutes: Mapped[Decimal] = mapped_column(sa.Numeric(), nullable=False, server_default=sa.text("30"))
    price_usd: Mapped[Decimal] = mapped_column(sa.Numeric(), nullable=False)
    price_updated_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")
    )

    __table_args__ = (
        sa.CheckConstraint(
            " AND ".join(
                f"{column} > 0 AND {column} NOT IN ('NaN'::numeric, 'Infinity'::numeric)" for column in _PRICE_COLUMNS
            ),
            name="ck_competition_upload_prices_positive",
        ),
        sa.CheckConstraint(
            "target_per_hour * half_life_minutes / 60 >= 1.71",
            name="ck_competition_upload_prices_max_multiplier",
        ),
    )


class BurnBalanceEntry(Base):
    """Append-only ledger of a coldkey's burn balance: burns and refunds credit, purchases debit."""

    __tablename__ = "burn_balance_entries"

    entry_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    miner_coldkey: Mapped[str] = mapped_column(sa.Text, nullable=False, index=True)
    quote_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), sa.ForeignKey("upload_payment_quotes.quote_id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    amount_alpha_rao: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")
    )

    __table_args__ = (
        sa.UniqueConstraint("quote_id", "kind", name="uq_burn_balance_entries_quote_kind"),
        sa.CheckConstraint("kind IN ('burn', 'purchase', 'refund')", name="ck_burn_balance_entries_kind"),
        sa.CheckConstraint(
            "(kind = 'purchase' AND amount_alpha_rao < 0) OR (kind <> 'purchase' AND amount_alpha_rao > 0)",
            name="ck_burn_balance_entries_sign",
        ),
    )
