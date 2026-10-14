from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, CreatedAtMixin


class EmissionSnapshot(Base, CreatedAtMixin):
    __tablename__ = "emission_snapshots"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    hotkey: Mapped[str] = mapped_column(sa.Text, nullable=False)
    agent_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("agents.agent_id"), nullable=False)
    set_id: Mapped[int] = mapped_column(sa.Integer, sa.ForeignKey("competitions.set_id"), nullable=False)
    block_number: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    emission: Mapped[float] = mapped_column(sa.Float, nullable=False)

    __table_args__ = (
        sa.UniqueConstraint("hotkey", "block_number", name="uq_emission_snapshots_hotkey_block"),
        sa.Index("idx_emission_snapshots_agent_set", "agent_id", "set_id"),
    )
