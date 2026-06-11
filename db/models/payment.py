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
    agent_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("agents.agent_id"),
        nullable=True,
        comment="Agent ID associated with this evaluation payment. The payment row is first created with no agent ID to claim an evaluation payment for a specific block hash + extrinsic index.",
    )
    miner_hotkey: Mapped[str] = mapped_column(sa.Text, nullable=False)
    miner_coldkey: Mapped[str] = mapped_column(sa.Text, nullable=False)
    amount_rao: Mapped[int] = mapped_column(sa.Integer, nullable=False)

    __table_args__ = (sa.PrimaryKeyConstraint("payment_block_hash", "payment_extrinsic_index"),)


class UploadPaymentQuote(Base, CreatedAtMixin):
    __tablename__ = "upload_payment_quotes"

    quote_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )
    miner_hotkey: Mapped[str] = mapped_column(sa.Text, nullable=False)
    amount_rao: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    send_address: Mapped[str] = mapped_column(sa.Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=False)
