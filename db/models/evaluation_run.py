from datetime import datetime
from typing import Any, Optional
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.models.enums import EvaluationRunLogType, EvaluationRunStatus


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"

    evaluation_run_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    evaluation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("evaluations.evaluation_id"),
        nullable=False,
    )
    problem_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    benchmark_family: Mapped[Optional[str]] = mapped_column(sa.Text)
    execution_spec: Mapped[Optional[Any]] = mapped_column(JSONB)
    status: Mapped[Optional[EvaluationRunStatus]] = mapped_column(
        sa.Enum(EvaluationRunStatus, name="evaluationrunstatus")
    )
    patch: Mapped[Optional[str]] = mapped_column(sa.Text)
    test_results: Mapped[Optional[Any]] = mapped_column(JSONB)
    verifier_reward: Mapped[Optional[float]] = mapped_column(sa.Double)
    cost_usd: Mapped[Optional[float]] = mapped_column(sa.Double)
    error_code: Mapped[Optional[int]] = mapped_column(sa.Integer)
    error_message: Mapped[Optional[str]] = mapped_column(sa.Text)
    # No server_default — the application always supplies this explicitly.
    created_at: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=False)
    started_initializing_agent_at: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True))
    started_running_agent_at: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True))
    started_initializing_eval_at: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True))
    started_running_eval_at: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True))
    finished_or_errored_at: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True))

    __table_args__ = (sa.Index("idx_evaluation_runs_evaluation_id", "evaluation_id"),)


class EvaluationRunLog(Base):
    __tablename__ = "evaluation_run_logs"

    evaluation_run_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    logs: Mapped[Optional[str]] = mapped_column(sa.Text)
    type: Mapped[Optional[EvaluationRunLogType]] = mapped_column(
        sa.Enum(EvaluationRunLogType, name="evaluationrunlogtype")
    )

    __table_args__ = (sa.PrimaryKeyConstraint("evaluation_run_id", "type"),)
