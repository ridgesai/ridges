from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e6b1f0c3d9a2"
down_revision: Union[str, Sequence[str], None] = "2d3d67111e36"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OPERATIONS_BEFORE = (
    "'state', 'policy', 'allocation', 'metadata', 'validator_concurrency', 'validator_scheduling', "
    "'validator_allowlist'"
)
_OPERATIONS_AFTER = _OPERATIONS_BEFORE + ", 'benchmark_phase_start', 'benchmark_phase_end'"

_REVIEW_STATUS_VIEW_WITH_BENCHMARK = """
CREATE OR REPLACE VIEW agent_final_review_statuses AS
SELECT
    approval_state.agent_id,
    approval_state.set_id,
    CASE
        WHEN job.status = 'pending' THEN 'pending'
        WHEN job.status IN ('running', 'error') THEN 'processing'
        WHEN job.status = 'needs_review' THEN 'under_review'
        WHEN approval_state.system_verdict = 'approved'
            AND pool.phase_id IS NOT NULL
            AND NOT EXISTS (
                SELECT 1
                FROM approved_agents approval
                WHERE approval.agent_id = approval_state.agent_id
                  AND approval.set_id = approval_state.set_id
            )
            THEN CASE WHEN pool.ended_at IS NULL THEN 'benchmark_pool' ELSE 'benchmark_not_selected' END
        WHEN approval_state.system_verdict = 'approved' THEN 'approved'
        WHEN approval_state.system_verdict = 'rejected' THEN 'rejected'
        ELSE 'under_review'
    END AS approval_review_status,
    approval_state.updated_at
FROM agent_approval_states approval_state
LEFT JOIN approval_jobs job ON job.job_id = approval_state.latest_job_id
LEFT JOIN LATERAL (
    SELECT entry.phase_id, phase.ended_at
    FROM benchmark_phase_entries entry
    JOIN competition_benchmark_phases phase ON phase.phase_id = entry.phase_id
    WHERE entry.agent_id = approval_state.agent_id
      AND entry.set_id = approval_state.set_id
    ORDER BY entry.entered_at DESC
    LIMIT 1
) pool ON TRUE
"""

_REVIEW_STATUS_VIEW_ORIGINAL = """
CREATE OR REPLACE VIEW agent_final_review_statuses AS
SELECT
    approval_state.agent_id,
    approval_state.set_id,
    CASE
        WHEN job.status = 'pending' THEN 'pending'
        WHEN job.status IN ('running', 'error') THEN 'processing'
        WHEN job.status = 'needs_review' THEN 'under_review'
        WHEN approval_state.system_verdict = 'approved' THEN 'approved'
        WHEN approval_state.system_verdict = 'rejected' THEN 'rejected'
        ELSE 'under_review'
    END AS approval_review_status,
    approval_state.updated_at
FROM agent_approval_states approval_state
LEFT JOIN approval_jobs job ON job.job_id = approval_state.latest_job_id
"""


def upgrade() -> None:
    op.create_table(
        "competition_benchmark_phases",
        sa.Column(
            "phase_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("set_id", sa.Integer(), sa.ForeignKey("competitions.set_id"), nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("started_by", sa.Text(), nullable=False),
        sa.Column("start_reason", sa.Text(), nullable=False),
        sa.Column("ended_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("ended_by", sa.Text(), nullable=True),
        sa.Column("end_reason", sa.Text(), nullable=True),
        sa.Column("winner_agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(
            "(ended_at IS NULL) = (ended_by IS NULL) AND (ended_at IS NULL) = (end_reason IS NULL)",
            name="ck_competition_benchmark_phases_end",
        ),
        sa.CheckConstraint(
            "winner_agent_id IS NULL OR ended_at IS NOT NULL",
            name="ck_competition_benchmark_phases_winner",
        ),
    )
    op.create_index(
        "uq_competition_benchmark_phases_active",
        "competition_benchmark_phases",
        ["set_id"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.create_table(
        "benchmark_phase_entries",
        sa.Column(
            "phase_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("competition_benchmark_phases.phase_id"),
            nullable=False,
        ),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("set_id", sa.Integer(), nullable=False),
        sa.Column("entered_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("phase_id", "agent_id"),
        sa.ForeignKeyConstraint(
            ["agent_id", "set_id"],
            ["agents.agent_id", "agents.set_id"],
            name="fk_benchmark_phase_entries_agent_competition",
        ),
    )
    op.create_index("idx_benchmark_phase_entries_agent", "benchmark_phase_entries", ["agent_id", "set_id"])
    op.create_foreign_key(
        "fk_competition_benchmark_phases_winner_entry",
        "competition_benchmark_phases",
        "benchmark_phase_entries",
        ["phase_id", "winner_agent_id"],
        ["phase_id", "agent_id"],
    )

    op.add_column("approved_agents", sa.Column("benchmark_phase_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_approved_agents_benchmark_phase",
        "approved_agents",
        "competition_benchmark_phases",
        ["benchmark_phase_id"],
        ["phase_id"],
    )
    op.create_index(
        "uq_approved_agents_benchmark_phase",
        "approved_agents",
        ["benchmark_phase_id"],
        unique=True,
        postgresql_where=sa.text("benchmark_phase_id IS NOT NULL"),
    )

    op.drop_constraint("ck_competition_admin_events_operation", "competition_admin_events", type_="check")
    op.create_check_constraint(
        "ck_competition_admin_events_operation",
        "competition_admin_events",
        f"operation IN ({_OPERATIONS_AFTER})",
    )
    op.execute(_REVIEW_STATUS_VIEW_WITH_BENCHMARK)


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM competition_benchmark_phases")):
        raise RuntimeError("Cannot downgrade while benchmark phases exist: winners would lose their benchmark basis.")

    op.execute(_REVIEW_STATUS_VIEW_ORIGINAL)
    op.execute(
        "DELETE FROM competition_admin_events WHERE operation IN ('benchmark_phase_start', 'benchmark_phase_end')"
    )
    op.drop_constraint("ck_competition_admin_events_operation", "competition_admin_events", type_="check")
    op.create_check_constraint(
        "ck_competition_admin_events_operation",
        "competition_admin_events",
        f"operation IN ({_OPERATIONS_BEFORE})",
    )
    op.drop_index("uq_approved_agents_benchmark_phase", table_name="approved_agents")
    op.drop_constraint("fk_approved_agents_benchmark_phase", "approved_agents", type_="foreignkey")
    op.drop_column("approved_agents", "benchmark_phase_id")
    op.drop_constraint(
        "fk_competition_benchmark_phases_winner_entry", "competition_benchmark_phases", type_="foreignkey"
    )
    op.drop_table("benchmark_phase_entries")
    op.drop_table("competition_benchmark_phases")
