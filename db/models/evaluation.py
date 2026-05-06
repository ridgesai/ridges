from datetime import datetime
from typing import Optional
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, CreatedAtMixin
from db.models.enums import EvaluationSetGroup


class Evaluation(Base, CreatedAtMixin):
    __tablename__ = "evaluations"

    evaluation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    agent_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("agents.agent_id"), nullable=False)
    validator_hotkey: Mapped[str] = mapped_column(sa.Text, nullable=False)
    set_id: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True))
    evaluation_set_group: Mapped[EvaluationSetGroup] = mapped_column(
        sa.Enum(EvaluationSetGroup, name="evaluationsetgroup"), nullable=False
    )

    __table_args__ = (
        sa.Index("idx_evaluations_id", "evaluation_id"),
        sa.Index("idx_evaluations_agent_id", "agent_id"),
        sa.Index(
            "idx_evaluations_set_group_agent_id",
            "evaluation_set_group",
            "agent_id",
        ),
        sa.Index(
            "idx_evaluations_validator_pattern",
            "validator_hotkey",
            postgresql_ops={"validator_hotkey": "text_pattern_ops"},
        ),
    )


class ApprovedAgent(Base):
    __tablename__ = "approved_agents"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )
    agent_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True), sa.ForeignKey("agents.agent_id"))
    set_id: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    approved_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("NOW()"),
    )

    __table_args__ = (sa.UniqueConstraint("agent_id", "set_id"),)
