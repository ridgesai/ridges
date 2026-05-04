"""verifier reward contract: add verifier_reward to evaluation_runs, update hydrated view

Revision ID: b8d3f6e2c9a5
Revises: a7c2e5f1d8b4
Create Date: 2026-04-16 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "b8d3f6e2c9a5"
down_revision: Union[str, None] = "a7c2e5f1d8b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("evaluation_runs", sa.Column("verifier_reward", sa.Double(), nullable=True))

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
    """)


def downgrade() -> None:
    # PostgreSQL does not allow CREATE OR REPLACE VIEW to remove columns.
    # Drop the full dependent-view chain, then rebuild from scratch.
    op.execute("DROP VIEW IF EXISTS validator_queue;")
    op.execute("DROP VIEW IF EXISTS screener_2_queue;")
    op.execute("DROP VIEW IF EXISTS screener_1_queue;")
    op.execute("DROP VIEW IF EXISTS evaluations_hydrated;")
    op.execute("DROP VIEW IF EXISTS evaluation_runs_hydrated;")

    op.execute("""
        CREATE VIEW evaluation_runs_hydrated AS
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

    op.execute("""
        CREATE VIEW evaluations_hydrated AS
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
    """)

    op.execute("""
        CREATE VIEW screener_1_queue AS
        SELECT agents.agent_id, agents.status
        FROM agents
        WHERE agents.status = 'screening_1'
          AND NOT EXISTS (
            SELECT 1 FROM evaluations_hydrated
            WHERE evaluations_hydrated.agent_id = agents.agent_id
              AND evaluations_hydrated.status IN ('success', 'running')
              AND evaluations_hydrated.evaluation_set_group = 'screener_1'::evaluationsetgroup
          )
          AND agents.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
          AND agents.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
          AND agents.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
        ORDER BY agents.created_at ASC;
    """)

    op.execute("""
        CREATE VIEW screener_2_queue AS
        SELECT agents.agent_id, agents.status
        FROM agents
        WHERE agents.status = 'screening_2'
          AND NOT EXISTS (
            SELECT 1 FROM evaluations_hydrated
            WHERE evaluations_hydrated.agent_id = agents.agent_id
              AND evaluations_hydrated.status IN ('success', 'running')
              AND evaluations_hydrated.evaluation_set_group = 'screener_2'::evaluationsetgroup
          )
          AND agents.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
          AND agents.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
          AND agents.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
        ORDER BY agents.created_at ASC;
    """)

    op.execute("""
        CREATE VIEW validator_queue AS
        WITH
            validator_eval_counts AS (
                SELECT
                    agent_id,
                    COUNT(*) FILTER (WHERE status = 'running') AS num_running_evals,
                    COUNT(*) FILTER (WHERE status = 'success') AS num_finished_evals
                FROM evaluations_hydrated
                WHERE evaluations_hydrated.status IN ('success', 'running')
                  AND evaluations_hydrated.evaluation_set_group = 'validator'::evaluationsetgroup
                GROUP BY agent_id
            ),
            screener_2_scores AS (
                SELECT agent_id, MAX(score) AS score FROM evaluations_hydrated
                WHERE evaluations_hydrated.evaluation_set_group = 'screener_2'::evaluationsetgroup
                  AND evaluations_hydrated.status = 'success'
                GROUP BY agent_id
            )
        SELECT
            agent_id,
            status,
            COALESCE(num_running_evals, 0) as num_running_evals,
            COALESCE(num_finished_evals, 0) as num_finished_evals
        FROM agents
             INNER JOIN screener_2_scores USING (agent_id)
             LEFT JOIN validator_eval_counts USING (agent_id)
        WHERE
            agents.status = 'evaluating'
            AND COALESCE(num_running_evals, 0) + COALESCE(num_finished_evals, 0) < 3
            AND agents.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
            AND agents.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
            AND agents.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
        ORDER BY
            screener_2_scores.score DESC,
            agents.created_at ASC,
            num_finished_evals DESC;
    """)

    op.drop_column("evaluation_runs", "verifier_reward")
