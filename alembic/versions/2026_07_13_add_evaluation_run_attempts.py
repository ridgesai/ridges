"""Add evaluation_run_attempts and per-attempt logs

Revision ID: b3f1a9c4d210
Revises: b7e4d2c9a106
Create Date: 2026-07-13 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "b3f1a9c4d210"
down_revision: Union[str, Sequence[str], None] = "b7e4d2c9a106"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE evaluation_run_attempts (
            attempt_id UUID PRIMARY KEY,
            evaluation_run_id UUID NOT NULL REFERENCES evaluation_runs (evaluation_run_id),
            attempt_number INT NOT NULL,
            status evaluationrunstatus NOT NULL,
            error_code INT,
            error_message TEXT,
            cost_usd DOUBLE PRECISION,
            created_at TIMESTAMPTZ NOT NULL,
            started_initializing_agent_at TIMESTAMPTZ,
            started_running_agent_at TIMESTAMPTZ,
            started_initializing_eval_at TIMESTAMPTZ,
            started_running_eval_at TIMESTAMPTZ,
            finished_or_errored_at TIMESTAMPTZ,
            UNIQUE (evaluation_run_id, attempt_number)
        )
        """
    )
    op.execute("ALTER TABLE evaluation_run_logs ADD COLUMN attempt_number INT NOT NULL DEFAULT 1;")
    op.execute("ALTER TABLE evaluation_run_logs DROP CONSTRAINT evaluation_run_logs_pkey;")
    op.execute(
        "ALTER TABLE evaluation_run_logs "
        "ADD CONSTRAINT evaluation_run_logs_pkey PRIMARY KEY (evaluation_run_id, attempt_number, type);"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE evaluation_run_logs DROP CONSTRAINT evaluation_run_logs_pkey;")
    op.execute("ALTER TABLE evaluation_run_logs DROP COLUMN attempt_number;")
    op.execute(
        "ALTER TABLE evaluation_run_logs ADD CONSTRAINT evaluation_run_logs_pkey PRIMARY KEY (evaluation_run_id, type);"
    )
    op.execute("DROP TABLE evaluation_run_attempts;")
