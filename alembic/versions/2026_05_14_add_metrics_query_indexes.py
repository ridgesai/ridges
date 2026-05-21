"""Add indexes for evaluation run metrics query performance

Revision ID: a3f7c9d2e841
Revises: de54176da579
Create Date: 2026-05-14 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3f7c9d2e841"
down_revision: Union[str, None] = "de54176da579"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create Index Concurrently requires no active transaction. Running the
    # operation inside of the autocommit_block context manager allows us
    # to create the index outside of the transaction block
    with op.get_context().autocommit_block():
        op.execute("""
            CREATE INDEX CONCURRENTLY idx_evaluation_runs_eval_id_problem
            ON evaluation_runs(evaluation_id, problem_name);
        """)
        op.execute("""
            CREATE INDEX CONCURRENTLY idx_evaluations_set_id ON evaluations(set_id);
        """)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("""
            DROP INDEX CONCURRENTLY IF EXISTS idx_evaluation_runs_eval_id_problem;
        """)
        op.execute("""
            DROP INDEX CONCURRENTLY IF EXISTS idx_evaluations_set_id;
        """)
