from datetime import datetime
from typing import Any, Optional
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, CreatedAtMixin
from db.models.enums import EvaluationSetGroup


class EvaluationSet(Base, CreatedAtMixin):
    __tablename__ = "evaluation_sets"

    set_id: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    set_group: Mapped[EvaluationSetGroup] = mapped_column(
        sa.Enum(EvaluationSetGroup, name="evaluationsetgroup"), nullable=False
    )
    problem_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    problem_suite_name: Mapped[Optional[str]] = mapped_column(sa.Text)
    benchmark_family: Mapped[Optional[str]] = mapped_column(sa.Text)
    execution_spec: Mapped[Optional[Any]] = mapped_column(JSONB)

    __table_args__ = (sa.PrimaryKeyConstraint("set_id", "set_group", "problem_name"),)


class DisqualifiedProblem(Base):
    __tablename__ = "disqualified_problems"

    set_id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    set_group: Mapped[EvaluationSetGroup] = mapped_column(
        sa.Enum(EvaluationSetGroup, name="evaluationsetgroup"), primary_key=True
    )
    problem_name: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    reason: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")
    )

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["set_id", "set_group", "problem_name"],
            ["evaluation_sets.set_id", "evaluation_sets.set_group", "evaluation_sets.problem_name"],
            ondelete="CASCADE",
        ),
    )


class ProblemDisqualificationJob(Base):
    __tablename__ = "problem_disqualification_jobs"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    set_id: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    set_group: Mapped[EvaluationSetGroup] = mapped_column(
        sa.Enum(EvaluationSetGroup, name="evaluationsetgroup"), nullable=False
    )
    problem_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")
    )
    processed_at: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True))
    attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("0"))
    error: Mapped[Optional[str]] = mapped_column(sa.Text)
