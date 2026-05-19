"""Add auto-approval pipeline tables

Revision ID: b3f91c0d4e62
Revises: a6c9d2f4e801
Create Date: 2026-05-15 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b3f91c0d4e62"
down_revision: Union[str, Sequence[str], None] = "a6c9d2f4e801"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "approval_jobs",
        sa.Column(
            "job_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.agent_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("set_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("claimed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("claimed_by", sa.Text(), nullable=True),
        sa.Column("next_attempt_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("input_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("source_sha256", sa.Text(), nullable=True),
        sa.Column("aggregate_verdict", sa.Text(), nullable=True),
        sa.Column("aggregate_score", sa.Float(), nullable=True),
        sa.Column("aggregate_confidence", sa.Float(), nullable=True),
        sa.Column("aggregate_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'error', 'completed')",
            name="ck_approval_jobs_status",
        ),
        sa.CheckConstraint(
            "aggregate_verdict IS NULL OR aggregate_verdict IN ('approved', 'rejected', 'needs_review')",
            name="ck_approval_jobs_aggregate_verdict",
        ),
    )
    op.create_index(
        "idx_approval_jobs_active_agent_set",
        "approval_jobs",
        ["agent_id", "set_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running', 'error')"),
    )
    op.create_index(
        "idx_approval_jobs_claimable",
        "approval_jobs",
        ["status", "next_attempt_at", "created_at"],
    )
    op.create_index(
        "idx_approval_jobs_running_lease",
        "approval_jobs",
        ["status", "lease_expires_at"],
    )

    op.create_table(
        "approval_job_rounds",
        sa.Column(
            "round_result_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("approval_jobs.job_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("round_index", sa.Integer(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("approval_score", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("raw_response", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint("round_index BETWEEN 1 AND 3", name="ck_approval_job_rounds_round_index"),
        sa.CheckConstraint(
            "verdict IN ('approved', 'rejected', 'needs_review')",
            name="ck_approval_job_rounds_verdict",
        ),
        sa.UniqueConstraint("job_id", "round_index", name="uq_approval_job_rounds_job_id_round_index"),
    )

    op.create_table(
        "agent_approval_states",
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.agent_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("set_id", sa.Integer(), primary_key=True),
        sa.Column(
            "latest_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("approval_jobs.job_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("processing_status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("system_verdict", sa.Text(), nullable=True),
        sa.Column("system_score", sa.Float(), nullable=True),
        sa.Column("system_confidence", sa.Float(), nullable=True),
        sa.Column("system_summary", sa.Text(), nullable=True),
        sa.Column("published_verdict", sa.Text(), nullable=True),
        sa.Column("published_score", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "processing_status IN ('pending', 'running', 'error', 'completed')",
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

    op.execute(
        """
        CREATE VIEW agent_final_review_statuses AS
        SELECT
            approval_state.agent_id,
            approval_state.set_id,
            CASE
                WHEN approval_state.processing_status = 'pending' THEN 'pending'
                WHEN approval_state.processing_status IN ('running', 'error') THEN 'under_review'
                WHEN approval_state.system_verdict = 'approved' THEN 'approved'
                WHEN approval_state.system_verdict = 'rejected' THEN 'rejected'
                WHEN approval_state.system_verdict = 'needs_review' THEN 'under_review'
                ELSE 'under_review'
            END AS approval_review_status,
            approval_state.updated_at
        FROM agent_approval_states approval_state
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS agent_final_review_statuses")
    op.drop_table("agent_approval_states")
    op.drop_table("approval_job_rounds")
    op.drop_index("idx_approval_jobs_running_lease", table_name="approval_jobs")
    op.drop_index("idx_approval_jobs_claimable", table_name="approval_jobs")
    op.drop_index("idx_approval_jobs_active_agent_set", table_name="approval_jobs")
    op.drop_table("approval_jobs")
