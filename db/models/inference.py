from datetime import datetime
from typing import Any, Optional
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class Inference(Base):
    __tablename__ = "inferences"

    inference_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    evaluation_run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), sa.ForeignKey("evaluation_runs.evaluation_run_id"), nullable=False
    )
    provider: Mapped[str] = mapped_column(sa.Text, nullable=False)
    model: Mapped[str] = mapped_column(sa.Text, nullable=False)
    temperature: Mapped[float] = mapped_column(sa.Float, nullable=False)
    messages: Mapped[Any] = mapped_column(JSONB, nullable=False)
    status_code: Mapped[Optional[int]] = mapped_column(sa.Integer)
    response: Mapped[Optional[str]] = mapped_column(sa.Text)
    num_input_tokens: Mapped[Optional[int]] = mapped_column(sa.Integer)
    num_output_tokens: Mapped[Optional[int]] = mapped_column(sa.Integer)
    cost_usd: Mapped[Optional[float]] = mapped_column(sa.Float)
    request_received_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")
    )
    response_sent_at: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True))

    __table_args__ = (
        sa.Index(
            "idx_inferences_created_provider_range",
            "request_received_at",
            "provider",
            postgresql_include=["response_sent_at", "status_code", "num_input_tokens", "num_output_tokens", "cost_usd"],
            postgresql_where=sa.text("response_sent_at IS NOT NULL AND provider IS NOT NULL"),
        ),
    )


class Embedding(Base):
    __tablename__ = "embeddings"

    embedding_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    evaluation_run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), sa.ForeignKey("evaluation_runs.evaluation_run_id"), nullable=False
    )
    provider: Mapped[str] = mapped_column(sa.Text, nullable=False)
    model: Mapped[str] = mapped_column(sa.Text, nullable=False)
    input: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status_code: Mapped[Optional[int]] = mapped_column(sa.Integer)
    response: Mapped[Optional[Any]] = mapped_column(JSONB)
    num_input_tokens: Mapped[Optional[int]] = mapped_column(sa.Integer)
    cost_usd: Mapped[Optional[float]] = mapped_column(sa.Float)
    request_received_at: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=False)
    response_sent_at: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True))
