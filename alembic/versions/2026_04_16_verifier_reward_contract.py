"""verifier reward contract: add verifier_reward to evaluation_runs, update hydrated view

Revision ID: b8d3f6e2c9a5
Revises: a7c2e5f1d8b4
Create Date: 2026-04-16 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b8d3f6e2c9a5'
down_revision: Union[str, None] = 'a7c2e5f1d8b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('evaluation_runs', sa.Column('verifier_reward', sa.Double(), nullable=True))

    op.execute("""
        CREATE OR REPLACE VIEW evaluation_runs_hydrated AS
        SELECT
            evaluation_runs.evaluation_run_id,
            evaluation_runs.evaluation_id,
            evaluation_runs.problem_name,
            evaluation_runs.status,
            evaluation_runs.patch,
            evaluation_runs.test_results,
            evaluation_runs.error_code,
            evaluation_runs.error_message,
            evaluation_runs.created_at,
            evaluation_runs.started_initializing_agent_at,
            evaluation_runs.started_running_agent_at,
            evaluation_runs.started_initializing_eval_at,
            evaluation_runs.started_running_eval_at,
            evaluation_runs.finished_or_errored_at,
            CASE
                WHEN evaluation_runs.verifier_reward IS NOT NULL AND evaluation_runs.verifier_reward = 1 THEN true
                WHEN evaluation_runs.verifier_reward IS NOT NULL AND evaluation_runs.verifier_reward <= 0 THEN false
                WHEN evaluation_runs.verifier_reward IS NOT NULL THEN NULL
                WHEN evaluation_runs.test_results IS NULL THEN NULL
                WHEN jsonb_array_length(evaluation_runs.test_results) = 0 THEN NULL
                WHEN (
                    SELECT COUNT(*) FILTER (WHERE test->>'status' = 'pass')
                    FROM jsonb_array_elements(evaluation_runs.test_results) AS test
                ) = jsonb_array_length(evaluation_runs.test_results) THEN true
                ELSE false
            END AS solved,
            evaluation_runs.benchmark_family,
            evaluation_runs.execution_spec,
            evaluation_runs.verifier_reward
        FROM evaluation_runs;
    """)


def downgrade() -> None:
    op.execute("""
        CREATE OR REPLACE VIEW evaluation_runs_hydrated AS
        SELECT
            evaluation_runs.evaluation_run_id,
            evaluation_runs.evaluation_id,
            evaluation_runs.problem_name,
            evaluation_runs.status,
            evaluation_runs.patch,
            evaluation_runs.test_results,
            evaluation_runs.error_code,
            evaluation_runs.error_message,
            evaluation_runs.created_at,
            evaluation_runs.started_initializing_agent_at,
            evaluation_runs.started_running_agent_at,
            evaluation_runs.started_initializing_eval_at,
            evaluation_runs.started_running_eval_at,
            evaluation_runs.finished_or_errored_at,
            CASE
                WHEN evaluation_runs.test_results IS NULL THEN NULL
                WHEN jsonb_array_length(evaluation_runs.test_results) = 0 THEN NULL
                WHEN (
                    SELECT COUNT(*) FILTER (WHERE test->>'status' = 'pass')
                    FROM jsonb_array_elements(evaluation_runs.test_results) AS test
                ) = jsonb_array_length(evaluation_runs.test_results) THEN true
                ELSE false
            END AS solved
        FROM evaluation_runs;
    """)

    op.drop_column('evaluation_runs', 'verifier_reward')
