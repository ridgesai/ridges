"""Optimize screener_1_queue and screener_2_queue views

Revision ID: c4d9a2e7f813
Revises: b1d5e8a3f902
Create Date: 2026-06-08 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "c4d9a2e7f813"
down_revision: Union[str, Sequence[str], None] = "b1d5e8a3f902"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The previous definitions of these views drove their NOT EXISTS check through evaluations_hydrated, which GROUPs BY over an INNER JOIN of evaluations and evaluation_runs_hydrated (a view over the entire evaluation_runs table).

    # Since that aggregation can't be correlated on agent_id, Postgres had to compute the status for every evaluation in the relevant evaluation_set_group (aggregating over hundreds of thousands of evaluation_runs rows) before filtering by agent_id.

    # Rewritten below to filter evaluations by (evaluation_set_group, agent_id) first, then compute each matching evaluation's status with a correlated subquery scoped to its own runs.
    op.execute("""
        CREATE OR REPLACE VIEW screener_1_queue AS
        SELECT agents.agent_id, agents.status
        FROM agents
        WHERE agents.status = 'screening_1'
          AND NOT EXISTS (
            SELECT 1 FROM evaluations e
            WHERE e.agent_id = agents.agent_id
              AND e.evaluation_set_group = 'screener_1'::evaluationsetgroup
              AND (
                SELECT (CASE
                    WHEN COUNT(*) = 0 THEN NULL
                    WHEN EVERY(erh.status = 'finished' OR (erh.status = 'error' AND erh.error_code BETWEEN 1000 AND 1999)) THEN 'success'
                    WHEN EVERY(erh.status IN ('finished', 'error')) THEN 'failure'
                    ELSE 'running'
                END)::evaluationstatus
                FROM evaluation_runs_hydrated erh
                WHERE erh.evaluation_id = e.evaluation_id
              ) IN ('success', 'running')
          )
          AND agents.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
          AND agents.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
          AND agents.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
        ORDER BY agents.created_at ASC;
    """)

    op.execute("""
        CREATE OR REPLACE VIEW screener_2_queue AS
        SELECT agents.agent_id, agents.status
        FROM agents
        WHERE agents.status = 'screening_2'
          AND NOT EXISTS (
            SELECT 1 FROM evaluations e
            WHERE e.agent_id = agents.agent_id
              AND e.evaluation_set_group = 'screener_2'::evaluationsetgroup
              AND (
                SELECT (CASE
                    WHEN COUNT(*) = 0 THEN NULL
                    WHEN EVERY(erh.status = 'finished' OR (erh.status = 'error' AND erh.error_code BETWEEN 1000 AND 1999)) THEN 'success'
                    WHEN EVERY(erh.status IN ('finished', 'error')) THEN 'failure'
                    ELSE 'running'
                END)::evaluationstatus
                FROM evaluation_runs_hydrated erh
                WHERE erh.evaluation_id = e.evaluation_id
              ) IN ('success', 'running')
          )
          AND agents.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
          AND agents.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
          AND agents.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
        ORDER BY agents.created_at ASC;
    """)

    with op.get_context().autocommit_block():
        op.execute("""
          CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_agents_screening_status_created_at
          ON agents (status, created_at)
          WHERE status IN ('screening_1', 'screening_2');
      """)


def downgrade() -> None:
    op.execute("""
        CREATE OR REPLACE VIEW screener_1_queue AS
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
        CREATE OR REPLACE VIEW screener_2_queue AS
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

    with op.get_context().autocommit_block():
        op.execute("""
        DROP INDEX CONCURRENTLY IF EXISTS idx_agents_screening_status_created_at;
      """)
