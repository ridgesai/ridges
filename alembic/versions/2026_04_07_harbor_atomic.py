"""harbor atomic: add execution_spec/benchmark fields, restructure evaluation_sets PK

Revision ID: a7c2e5f1d8b4
Revises: 159d505b4ec8
Create Date: 2026-04-07 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a7c2e5f1d8b4"
down_revision: Union[str, None] = "159d505b4ec8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("evaluation_sets", sa.Column("problem_suite_name", sa.Text(), nullable=True))
    op.add_column("evaluation_sets", sa.Column("benchmark_family", sa.Text(), nullable=True))
    op.add_column(
        "evaluation_sets", sa.Column("execution_spec", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )

    op.add_column("evaluation_runs", sa.Column("benchmark_family", sa.Text(), nullable=True))
    op.add_column(
        "evaluation_runs", sa.Column("execution_spec", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )

    op.drop_table("infinite_swe_problems")

    op.execute("ALTER TABLE evaluation_sets DROP CONSTRAINT IF EXISTS evaluation_sets_pkey;")
    op.execute("ALTER TABLE evaluation_sets ALTER COLUMN problem_name SET NOT NULL;")
    op.execute("ALTER TABLE evaluation_sets ADD PRIMARY KEY (set_id, set_group, problem_name);")

    op.execute("""
        UPDATE evaluation_sets
        SET benchmark_family = COALESCE(
            benchmark_family,
            execution_spec->>'benchmark_family',
            execution_spec->>'problem_suite_name',
            problem_suite_name,
            'custom'
        )
        WHERE benchmark_family IS NULL OR benchmark_family = '';
    """)

    op.execute("""
        UPDATE evaluation_sets
        SET problem_suite_name = COALESCE(
            problem_suite_name,
            execution_spec->>'problem_suite_name',
            benchmark_family
        )
        WHERE problem_suite_name IS NULL OR problem_suite_name = '';
    """)

    op.execute("""
        UPDATE evaluation_runs
        SET benchmark_family = COALESCE(
            benchmark_family,
            execution_spec->>'benchmark_family',
            execution_spec->>'problem_suite_name',
            'custom'
        )
        WHERE benchmark_family IS NULL OR benchmark_family = '';
    """)


def downgrade() -> None:
    op.execute("UPDATE evaluation_runs SET benchmark_family = NULL;")
    op.execute("UPDATE evaluation_sets SET benchmark_family = NULL, problem_suite_name = NULL;")

    op.execute("ALTER TABLE evaluation_sets DROP CONSTRAINT IF EXISTS evaluation_sets_pkey;")
    op.execute("ALTER TABLE evaluation_sets ALTER COLUMN problem_name DROP NOT NULL;")
    op.execute("ALTER TABLE evaluation_sets ADD PRIMARY KEY (set_id);")

    op.create_table(
        "infinite_swe_problems",
        sa.Column("problem_name", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("problem_name"),
    )

    op.drop_column("evaluation_runs", "execution_spec")
    op.drop_column("evaluation_runs", "benchmark_family")
    op.drop_column("evaluation_sets", "execution_spec")
    op.drop_column("evaluation_sets", "benchmark_family")
    op.drop_column("evaluation_sets", "problem_suite_name")
