"""Add cost-based ranking tie-breaker views

Revision ID: a6c9d2f4e801
Revises: f4e8b7c2d901
Create Date: 2026-05-14 18:40:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "a6c9d2f4e801"
down_revision: Union[str, Sequence[str], None] = "f4e8b7c2d901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


EVALUATION_RUNS_HYDRATED_WITH_COST = """
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
        WHEN evaluation_runs.verifier_reward IS NOT NULL THEN evaluation_runs.verifier_reward >= 1
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
    evaluation_runs.verifier_reward,
    evaluation_runs.cost_usd
FROM evaluation_runs;
"""


EVALUATIONS_HYDRATED_WITH_COST = """
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
    ) FILTER (WHERE erh.solved) AS avg_running_secs,
    CASE
        WHEN COUNT(*) FILTER (WHERE erh.cost_usd IS NULL) > 0 THEN NULL
        ELSE AVG(erh.cost_usd)
    END AS avg_cost_usd
FROM evaluations
    INNER JOIN evaluation_runs_hydrated erh USING (evaluation_id)
GROUP BY evaluations.evaluation_id;
"""


EVALUATION_RUNS_HYDRATED_WITHOUT_COST = """
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
        WHEN evaluation_runs.verifier_reward IS NOT NULL THEN evaluation_runs.verifier_reward >= 1
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
"""


EVALUATIONS_HYDRATED_WITHOUT_COST = """
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


def upgrade() -> None:
    op.execute(EVALUATION_RUNS_HYDRATED_WITH_COST)
    op.execute(EVALUATIONS_HYDRATED_WITH_COST)


def downgrade() -> None:
    op.execute(EVALUATIONS_HYDRATED_WITHOUT_COST)
    op.execute(EVALUATION_RUNS_HYDRATED_WITHOUT_COST)
