"""add openrouter secrets and persisted evaluation run cost

Revision ID: 34e1f2c7a9bd
Revises: b8d3f6e2c9a5
Create Date: 2026-05-05 12:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "34e1f2c7a9bd"
down_revision: Union[str, None] = "b8d3f6e2c9a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_openrouter_secrets",
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("runtime_api_key_ciphertext", postgresql.BYTEA(), nullable=False),
        sa.Column("management_api_key_ciphertext", postgresql.BYTEA(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("api_key_label", sa.Text(), nullable=False),
        sa.Column("api_key_creator_user_id", sa.Text(), nullable=False),
        sa.Column("validated_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.agent_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("agent_id"),
    )

    op.add_column("evaluation_runs", sa.Column("cost_usd", sa.Double(), nullable=True))

    op.execute("DROP VIEW IF EXISTS evaluation_runs_with_cost;")
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


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS evaluation_runs_with_cost;")
    op.execute(
        """
        CREATE VIEW evaluation_runs_with_cost AS
        SELECT
            er.*,
            COALESCE(SUM(i.cost_usd), 0) AS total_cost_usd,
            COALESCE(SUM(i.num_input_tokens), 0) AS total_input_tokens,
            COALESCE(SUM(i.num_output_tokens), 0) AS total_output_tokens,
            COUNT(*) as num_inferences
        FROM evaluation_runs er
        LEFT JOIN inferences i ON er.evaluation_run_id = i.evaluation_run_id
        GROUP BY er.evaluation_run_id;
    """
    )

    op.drop_column("evaluation_runs", "cost_usd")
    op.drop_table("agent_openrouter_secrets")
