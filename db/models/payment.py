from datetime import datetime
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

    __table_args__ = (
        sa.CheckConstraint(
            "num_nonnulls(amount_rao, amount_alpha_rao) = 1",
            name="ck_amount_rao_xor_amount_alpha_rao",
        ),
    )
