"""add pre-screening judge pipeline tables

Revision ID: d8e2f4a6c1b0
Revises: c7d4e9a1b2f3
Create Date: 2026-05-07 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d8e2f4a6c1b0"
down_revision: Union[str, None] = "c7d4e9a1b2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE agentstatus ADD VALUE IF NOT EXISTS 'pre_screening'")
        op.execute("ALTER TYPE agentstatus ADD VALUE IF NOT EXISTS 'failed_pre_screening'")
        op.execute("ALTER TYPE agentstatus ADD VALUE IF NOT EXISTS 'pre_screening_needs_review'")

    op.execute(
        """
        UPDATE agents
        SET status = (
            CASE status::text
                WHEN 'llm_judging' THEN 'pre_screening'
                WHEN 'failed_llm_judge' THEN 'failed_pre_screening'
                WHEN 'llm_judge_needs_review' THEN 'pre_screening_needs_review'
            END
        )::agentstatus
        WHERE status::text IN ('llm_judging', 'failed_llm_judge', 'llm_judge_needs_review');
        """
    )

    op.create_table(
        "pre_screening_jobs",
        sa.Column("job_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.agent_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("claimed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("claimed_by", sa.Text(), nullable=True),
        sa.Column("next_attempt_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'error', 'succeeded', 'failed', 'needs_review')",
            name="ck_pre_screening_jobs_status",
        ),
    )
    op.create_index(
        "idx_pre_screening_jobs_active_agent",
        "pre_screening_jobs",
        ["agent_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running', 'error')"),
    )
    op.create_index(
        "idx_pre_screening_jobs_claimable",
        "pre_screening_jobs",
        ["status", "next_attempt_at", "created_at"],
    )
    op.create_index(
        "idx_pre_screening_jobs_running_lease",
        "pre_screening_jobs",
        ["status", "lease_expires_at"],
    )

    op.create_table(
        "pre_screening_results",
        sa.Column("result_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pre_screening_jobs.job_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.agent_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("categories", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("static_findings", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("fallback_used", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("source_sha256", sa.Text(), nullable=True),
        sa.Column("raw_response", postgresql.JSONB(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint("verdict IN ('pass', 'fail', 'needs_review')", name="ck_pre_screening_results_verdict"),
    )
    op.create_index(
        "idx_pre_screening_results_agent_id_created_at",
        "pre_screening_results",
        ["agent_id", "created_at"],
    )

    op.execute(
        """
        CREATE OR REPLACE VIEW pre_screening_queue AS
        SELECT agents.agent_id, agents.status
        FROM agents
        WHERE agents.status IN ('pre_screening', 'pre_screening_needs_review')
          AND agents.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
          AND agents.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
          AND agents.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
        ORDER BY agents.created_at ASC;
    """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS pre_screening_queue;")
    op.drop_index("idx_pre_screening_results_agent_id_created_at", table_name="pre_screening_results")
    op.drop_table("pre_screening_results")
    op.drop_index("idx_pre_screening_jobs_running_lease", table_name="pre_screening_jobs")
    op.drop_index("idx_pre_screening_jobs_claimable", table_name="pre_screening_jobs")
    op.drop_index("idx_pre_screening_jobs_active_agent", table_name="pre_screening_jobs")
    op.drop_table("pre_screening_jobs")
