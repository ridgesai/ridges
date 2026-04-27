from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
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
