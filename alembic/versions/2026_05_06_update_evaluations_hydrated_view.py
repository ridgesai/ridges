"""Update evaluations hydrated view

Revision ID: 234ed0606f2a
Revises: 34e1f2c7a9bd
Create Date: 2026-05-06 11:03:17.836098

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "234ed0606f2a"
down_revision: Union[str, Sequence[str], None] = "34e1f2c7a9bd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE VIEW evaluations_hydrated AS
        SELECT
            evaluations.*,
            (CASE
                 WHEN EVERY(erh.status = 'finished' OR (erh.status = 'error' AND erh.error_code BETWEEN 1000 AND 1999)) THEN 'success'
                 WHEN EVERY(erh.status IN ('finished', 'error')) THEN 'failure'
                 ELSE 'running'
                END)::evaluationstatus AS status,
            COUNT(*) FILTER (WHERE erh.solved)::float / COUNT(*) AS score,
            AVG(
                EXTRACT(EPOCH FROM (erh.finished_or_errored_at - erh.started_running_agent_at))
            ) FILTER (WHERE erh.solved) AS avg_running_secs
        FROM evaluations
            INNER JOIN evaluation_runs_hydrated erh USING (evaluation_id)
        GROUP BY evaluations.evaluation_id;
    """
    )


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE VIEW evaluations_hydrated AS
        SELECT
            evaluations.*,
            (CASE
                 WHEN EVERY(erh.status = 'finished' OR (erh.status = 'error' AND erh.error_code BETWEEN 1000 AND 1999)) THEN 'success'
                 WHEN EVERY(erh.status IN ('finished', 'error')) THEN 'failure'
                 ELSE 'running'
                END)::evaluationstatus AS status,
            COUNT(*) FILTER (WHERE erh.solved)::float / COUNT(*) AS score
        FROM evaluations
            INNER JOIN evaluation_runs_hydrated erh USING (evaluation_id)
        GROUP BY evaluations.evaluation_id;
    """
    )
