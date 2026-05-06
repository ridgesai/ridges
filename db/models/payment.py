from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, CreatedAtMixin


class EvaluationPayment(Base, CreatedAtMixin):
    __tablename__ = "evaluation_payments"

    payment_block_hash: Mapped[str] = mapped_column(sa.Text, nullable=False)
    payment_extrinsic_index: Mapped[str] = mapped_column(sa.Text, nullable=False)
    agent_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("agents.agent_id"), nullable=False)
    miner_hotkey: Mapped[str] = mapped_column(sa.Text, nullable=False)
    miner_coldkey: Mapped[str] = mapped_column(sa.Text, nullable=False)
    amount_rao: Mapped[int] = mapped_column(sa.Integer, nullable=False)

    __table_args__ = (sa.PrimaryKeyConstraint("payment_block_hash", "payment_extrinsic_index"),)
