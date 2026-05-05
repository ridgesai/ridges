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
    block_hash: Mapped[str] = mapped_column(sa.Text, nullable=False)
    amount: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    tx_hash: Mapped[str] = mapped_column(sa.Text, nullable=False)
    upload_tx_hash: Mapped[str] = mapped_column(sa.Text, nullable=False)
    upload_block_hash: Mapped[str] = mapped_column(sa.Text, nullable=False)
    updated_at: Mapped[sa.DateTime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("NOW()"),
    )
    coldkey: Mapped[str] = mapped_column(sa.Text, nullable=False)
    upload_amount: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
