from datetime import datetime
from typing import Optional
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base

APPROVAL_JOB_STATUSES = ("pending", "running", "error", "completed", "needs_review")
ACTIVE_APPROVAL_JOB_STATUSES = ("pending", "running", "error", "needs_review")
APPROVAL_VERDICTS = ("approved", "rejected", "needs_review")
APPROVAL_PROCESSING_STATUSES = ("pending", "running", "error", "completed", "needs_review")


class ApprovalJob(Base):
    __tablename__ = "approval_jobs"

    job_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    agent_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), sa.ForeignKey("agents.agent_id", ondelete="CASCADE"), nullable=False
    )
    set_id: Mapped[int] = mapped_column(sa.Integer, nullable=False)
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
    input_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    aggregate_verdict: Mapped[Optional[str]] = mapped_column(sa.Text)
    aggregate_score: Mapped[Optional[float]] = mapped_column(sa.Float)
    aggregate_confidence: Mapped[Optional[float]] = mapped_column(sa.Float)
    aggregate_summary: Mapped[Optional[str]] = mapped_column(sa.Text)
    projected_at: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True))
    decision_source: Mapped[Optional[str]] = mapped_column(sa.Text)
    discord_channel_id: Mapped[Optional[str]] = mapped_column(sa.Text)
    discord_message_id: Mapped[Optional[str]] = mapped_column(sa.Text)
    discord_thread_id: Mapped[Optional[str]] = mapped_column(sa.Text)
    review_requested_at: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True))
    reviewer_id: Mapped[Optional[str]] = mapped_column(sa.Text)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True))
    review_decision_reason: Mapped[Optional[str]] = mapped_column(sa.Text)
    announcement_sent_at: Mapped[Optional[datetime]] = mapped_column(sa.TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")
    )

    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'error', 'completed', 'needs_review')",
            name="ck_approval_jobs_status",
        ),
        sa.CheckConstraint(
            "aggregate_verdict IS NULL OR aggregate_verdict IN ('approved', 'rejected', 'needs_review')",
            name="ck_approval_jobs_aggregate_verdict",
        ),
        sa.CheckConstraint(
            "decision_source IS NULL OR decision_source IN ('auto_judge', 'discord_review')",
            name="ck_approval_jobs_decision_source",
        ),
        sa.CheckConstraint(
            "(discord_channel_id IS NULL) = (discord_message_id IS NULL)",
            name="ck_approval_jobs_discord_message_pair",
        ),
        sa.CheckConstraint(
            "discord_thread_id IS NULL OR ("
            "discord_channel_id IS NOT NULL AND discord_message_id IS NOT NULL"
            ")",
            name="ck_approval_jobs_discord_thread_requires_message",
        ),
        sa.Index(
            "idx_approval_jobs_active_agent_set",
            "agent_id",
            "set_id",
            unique=True,
            postgresql_where=sa.text("status IN ('pending', 'running', 'error', 'needs_review')"),
        ),
        sa.Index("idx_approval_jobs_claimable", "status", "next_attempt_at", "created_at"),
        sa.Index("idx_approval_jobs_running_lease", "status", "lease_expires_at"),
        sa.Index(
            "idx_approval_jobs_unprojected_review_states",
            "created_at",
            postgresql_where=sa.text("status IN ('needs_review', 'completed') AND projected_at IS NULL"),
        ),
        sa.Index(
            "idx_approval_jobs_pending_announcement",
            "created_at",
            postgresql_where=sa.text(
                "status = 'completed' "
                "AND decision_source = 'auto_judge' "
                "AND announcement_sent_at IS NULL"
            ),
        ),
    )


class ApprovalJobRound(Base):
    __tablename__ = "approval_job_rounds"

    round_result_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    job_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), sa.ForeignKey("approval_jobs.job_id", ondelete="CASCADE"), nullable=False
    )
    round_index: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    model: Mapped[str] = mapped_column(sa.Text, nullable=False)
    verdict: Mapped[str] = mapped_column(sa.Text, nullable=False)
    approval_score: Mapped[float] = mapped_column(sa.Float, nullable=False)
    confidence: Mapped[float] = mapped_column(sa.Float, nullable=False)
    summary: Mapped[str] = mapped_column(sa.Text, nullable=False)
    evidence: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=sa.text("'[]'::jsonb"))
    raw_response: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")
    )

    __table_args__ = (
        sa.CheckConstraint("round_index BETWEEN 1 AND 3", name="ck_approval_job_rounds_round_index"),
        sa.CheckConstraint(
            "verdict IN ('approved', 'rejected', 'needs_review')",
            name="ck_approval_job_rounds_verdict",
        ),
        sa.UniqueConstraint("job_id", "round_index", name="uq_approval_job_rounds_job_id_round_index"),
    )


class AgentApprovalState(Base):
    __tablename__ = "agent_approval_states"

    agent_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("agents.agent_id", ondelete="CASCADE"),
        primary_key=True,
    )
    set_id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    latest_job_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), sa.ForeignKey("approval_jobs.job_id", ondelete="SET NULL")
    )
    processing_status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("'pending'"))
    system_verdict: Mapped[Optional[str]] = mapped_column(sa.Text)
    system_score: Mapped[Optional[float]] = mapped_column(sa.Float)
    system_confidence: Mapped[Optional[float]] = mapped_column(sa.Float)
    system_summary: Mapped[Optional[str]] = mapped_column(sa.Text)
    published_verdict: Mapped[Optional[str]] = mapped_column(sa.Text)
    published_score: Mapped[Optional[float]] = mapped_column(sa.Float)
    updated_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")
    )

    __table_args__ = (
        sa.CheckConstraint(
            "processing_status IN ('pending', 'running', 'error', 'completed', 'needs_review')",
            name="ck_agent_approval_states_processing_status",
        ),
        sa.CheckConstraint(
            "system_verdict IS NULL OR system_verdict IN ('approved', 'rejected', 'needs_review')",
            name="ck_agent_approval_states_system_verdict",
        ),
        sa.CheckConstraint(
            "published_verdict IS NULL OR published_verdict IN ('approved', 'rejected', 'needs_review')",
            name="ck_agent_approval_states_published_verdict",
        ),
    )
