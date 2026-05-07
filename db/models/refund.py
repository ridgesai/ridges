from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, CreatedAtMixin


class FailedUploadRefund(Base, CreatedAtMixin):
    __tablename__ = "failed_upload_refunds"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )
    # Refund transaction details
    tx_hash: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        comment="The transaction hash of the refund transaction",
    )
    block_hash: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        comment="The block hash of the refund transaction",
    )
    block_extrinsic_index: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        comment="The block extrinsic index of the transaction associated with the agent upload that failed and caused the refund",
    )
    amount: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, comment="The amount to refund in RAO")
    # Upload transaction details
    upload_tx_hash: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        comment="The transaction hash of the transaction associated with the agent upload that failed and caused the refund",
    )
    upload_block_hash: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        comment="The block hash of the transaction associated with the agent upload that failed and caused the refund",
    )
    upload_block_extrinsic_index: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        comment="The block extrinsic index of the transaction associated with the agent upload that failed and caused the refund",
    )
    upload_amount: Mapped[int] = mapped_column(
        sa.BigInteger,
        nullable=False,
        comment="The transfer amount in RAO of the transaction associated with the agent upload that failed and caused the refund",
    )
    coldkey: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        comment="The coldkey of the miner for which the upload failed and caused the refund",
    )
    updated_at: Mapped[sa.DateTime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("NOW()"),
    )
