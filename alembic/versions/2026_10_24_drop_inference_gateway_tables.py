"""drop inference gateway tables

Revision ID: c5fc32cc83fc
Revises: 2d3d67111e36
Create Date: 2026-10-24 03:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c5fc32cc83fc"
down_revision: Union[str, Sequence[str], None] = "2d3d67111e36"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP VIEW IF EXISTS evaluation_runs_with_cost;")
    op.execute("DROP TABLE IF EXISTS embeddings;")
    op.execute("DROP TABLE IF EXISTS inferences;")


def downgrade() -> None:
    op.create_table(
        "inferences",
        sa.Column(
            "inference_id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("evaluation_run_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False),
        sa.Column("messages", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("response", sa.Text(), nullable=True),
        sa.Column("num_input_tokens", sa.Integer(), nullable=True),
        sa.Column("num_output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column(
            "request_received_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column("response_sent_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["evaluation_run_id"], ["evaluation_runs.evaluation_run_id"]),
        sa.PrimaryKeyConstraint("inference_id"),
    )
    op.create_index(
        "idx_inferences_created_provider_range",
        "inferences",
        ["request_received_at", "provider"],
        postgresql_include=[
            "response_sent_at",
            "status_code",
            "num_input_tokens",
            "num_output_tokens",
            "cost_usd",
        ],
        postgresql_where=sa.text("response_sent_at IS NOT NULL AND provider IS NOT NULL"),
    )

    op.create_table(
        "embeddings",
        sa.Column("embedding_id", sa.UUID(), nullable=False),
        sa.Column("evaluation_run_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("input", sa.Text(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("num_input_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("request_received_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("response_sent_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["evaluation_run_id"], ["evaluation_runs.evaluation_run_id"]),
        sa.PrimaryKeyConstraint("embedding_id"),
    )

    op.execute(
        """
        CREATE VIEW evaluation_runs_with_cost AS
        SELECT
            er.*,
            COALESCE(er.cost_usd, 0) AS total_cost_usd,
            COALESCE(SUM(i.num_input_tokens), 0) AS total_input_tokens,
            COALESCE(SUM(i.num_output_tokens), 0) AS total_output_tokens,
            COUNT(*) as num_inferences
        FROM evaluation_runs er
        LEFT JOIN inferences i ON er.evaluation_run_id = i.evaluation_run_id
        GROUP BY er.evaluation_run_id;
    """
    )
