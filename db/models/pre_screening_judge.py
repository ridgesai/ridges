from datetime import datetime
from typing import Optional
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class PreScreeningJob(Base):
    __tablename__ = "pre_screening_jobs"

    job_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    agent_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), sa.ForeignKey("agents.agent_id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("'pending'"))
    attempt_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("0"))
    claim_token: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True))
    claimed_at: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True))
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True))
    claimed_by: Mapped[Optional[str]] = mapped_column(sa.Text)
    next_attempt_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")
    )
    last_error: Mapped[Optional[str]] = mapped_column(sa.Text)
    policy_version: Mapped[str] = mapped_column(sa.Text, nullable=False)
    projected_at: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True))
    discord_channel_id: Mapped[Optional[str]] = mapped_column(sa.Text)
    discord_message_id: Mapped[Optional[str]] = mapped_column(sa.Text)
    discord_thread_id: Mapped[Optional[str]] = mapped_column(sa.Text)
    reviewer_id: Mapped[Optional[str]] = mapped_column(sa.Text)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True))
    review_requested_at: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True))
    announcement_sent_at: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")
    )

    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'error', 'succeeded', 'failed', 'needs_review')",
            name="ck_pre_screening_jobs_status",
        ),
        sa.CheckConstraint(
            "(discord_channel_id IS NULL) = (discord_message_id IS NULL)",
            name="ck_pre_screening_jobs_discord_message_pair",
        ),
        sa.CheckConstraint(
            "discord_thread_id IS NULL OR (discord_channel_id IS NOT NULL AND discord_message_id IS NOT NULL)",
            name="ck_pre_screening_jobs_discord_thread_requires_message",
        ),
        sa.CheckConstraint(
            "(reviewer_id IS NULL) = (reviewed_at IS NULL)",
            name="ck_pre_screening_jobs_reviewer_pair",
        ),
        sa.CheckConstraint(
            "reviewed_at IS NULL OR status IN ('succeeded', 'failed')",
            name="ck_pre_screening_jobs_reviewer_requires_terminal_status",
        ),
        sa.Index(
            "idx_pre_screening_jobs_active_agent",
            "agent_id",
            unique=True,
            postgresql_where=sa.text("status IN ('pending', 'running', 'error')"),
        ),
        sa.Index("idx_pre_screening_jobs_claimable", "status", "next_attempt_at", "created_at"),
        sa.Index("idx_pre_screening_jobs_running_lease", "status", "lease_expires_at"),
        sa.Index(
            "idx_pre_screening_jobs_unprojected_terminal",
            "created_at",
            postgresql_where=sa.text("status IN ('succeeded', 'failed', 'needs_review') AND projected_at IS NULL"),
        ),
        sa.Index(
            "idx_pre_screening_jobs_needs_review_discord_unposted",
            "review_requested_at",
            "created_at",
            postgresql_where=sa.text("status = 'needs_review' AND discord_message_id IS NULL"),
        ),
        sa.Index(
            "idx_pre_screening_jobs_pending_announcement",
            "created_at",
            postgresql_where=sa.text("status = 'failed' AND announcement_sent_at IS NULL AND reviewer_id IS NULL"),
        ),
    )


class PreScreeningResult(Base):
    __tablename__ = "pre_screening_results"

    result_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    job_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), sa.ForeignKey("pre_screening_jobs.job_id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), sa.ForeignKey("agents.agent_id", ondelete="CASCADE"), nullable=False
    )
    verdict: Mapped[str] = mapped_column(sa.Text, nullable=False)
    confidence: Mapped[float] = mapped_column(sa.Float, nullable=False)
    summary: Mapped[str] = mapped_column(sa.Text, nullable=False)
    categories: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=sa.text("'[]'::jsonb"))
    evidence: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=sa.text("'[]'::jsonb"))
    static_findings: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=sa.text("'[]'::jsonb"))
    model: Mapped[Optional[str]] = mapped_column(sa.Text)
    fallback_used: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.text("false"))
    policy_version: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source_sha256: Mapped[Optional[str]] = mapped_column(sa.Text)
    raw_response: Mapped[Optional[dict]] = mapped_column(JSONB)
    error_message: Mapped[Optional[str]] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")
    )

    __table_args__ = (
        sa.CheckConstraint("verdict IN ('pass', 'fail', 'needs_review')", name="ck_pre_screening_results_verdict"),
        sa.Index("idx_pre_screening_results_agent_id_created_at", "agent_id", "created_at"),
    )
