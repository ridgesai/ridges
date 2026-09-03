"""Add competition work cursors and scope queue views.

Revision ID: 06f4bede4ef6
Revises: 6c63d9349cfc
Create Date: 2026-08-21 07:41:54.786534

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "06f4bede4ef6"
down_revision: Union[str, Sequence[str], None] = "6c63d9349cfc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _replace_queue_views(*, competition_scoped: bool) -> None:
    competition_join = "INNER JOIN competitions ON competitions.set_id = agents.set_id" if competition_scoped else ""
    competition_predicate = (
        """
          AND competitions.start_date IS NOT NULL
          AND competitions.end_date IS NULL
          AND competitions.is_paused IS FALSE
          AND competitions.scoring_mode IS NOT NULL"""
        if competition_scoped
        else ""
    )
    evaluation_set_predicate = "AND e.set_id = agents.set_id" if competition_scoped else ""

    op.execute(f"""
        CREATE OR REPLACE VIEW pre_screening_queue AS
        SELECT agents.agent_id, agents.status
        FROM agents
        {competition_join}
        WHERE agents.status IN ('pre_screening', 'pre_screening_needs_review')
          {competition_predicate}
          AND agents.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
          AND NOT EXISTS (
            SELECT 1
            FROM banned_coldkeys
            WHERE banned_coldkeys.miner_coldkey = agents.miner_coldkey
          )
          AND agents.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
        ORDER BY agents.created_at ASC;
    """)

    for queue_name, status, set_group in (
        ("screener_1_queue", "screening_1", "screener_1"),
        ("screener_2_queue", "screening_2", "screener_2"),
    ):
        op.execute(f"""
            CREATE OR REPLACE VIEW {queue_name} AS
            SELECT agents.agent_id, agents.status
            FROM agents
            {competition_join}
            WHERE agents.status = '{status}'
              {competition_predicate}
              AND NOT EXISTS (
                SELECT 1 FROM evaluations e
                WHERE e.agent_id = agents.agent_id
                  {evaluation_set_predicate}
                  AND e.evaluation_set_group = '{set_group}'::evaluationsetgroup
                  AND (
                    SELECT (CASE
                        WHEN COUNT(*) = 0 THEN NULL
                        WHEN EVERY(
                            erh.status = 'finished'
                            OR (erh.status = 'error' AND erh.error_code BETWEEN 1000 AND 1999)
                        ) THEN 'success'
                        WHEN EVERY(erh.status IN ('finished', 'error')) THEN 'failure'
                        ELSE 'running'
                    END)::evaluationstatus
                    FROM evaluation_runs_hydrated erh
                    WHERE erh.evaluation_id = e.evaluation_id
                  ) IN ('success', 'running')
              )
              AND agents.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
              AND NOT EXISTS (
                SELECT 1
                FROM banned_coldkeys
                WHERE banned_coldkeys.miner_coldkey = agents.miner_coldkey
              )
              AND agents.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
            ORDER BY agents.created_at ASC;
        """)

    validator_set_columns = ", set_id" if competition_scoped else ""
    validator_set_join = "AND validator_eval_counts.set_id = agents.set_id" if competition_scoped else ""
    screener_set_join = "AND screener_2_scores.set_id = agents.set_id" if competition_scoped else ""
    validator_limit = "competitions.required_validator_count" if competition_scoped else "3"
    op.execute(f"""
        CREATE OR REPLACE VIEW validator_queue AS
        WITH
            validator_eval_counts AS (
                SELECT
                    agent_id{validator_set_columns},
                    COUNT(*) FILTER (WHERE status = 'running') AS num_running_evals,
                    COUNT(*) FILTER (WHERE status = 'success') AS num_finished_evals
                FROM evaluations_hydrated
                WHERE evaluations_hydrated.status IN ('success', 'running')
                  AND evaluations_hydrated.evaluation_set_group = 'validator'::evaluationsetgroup
                GROUP BY agent_id{validator_set_columns}
            ),
            screener_2_scores AS (
                SELECT agent_id{validator_set_columns}, MAX(score) AS score
                FROM evaluations_hydrated
                WHERE evaluations_hydrated.evaluation_set_group = 'screener_2'::evaluationsetgroup
                  AND evaluations_hydrated.status = 'success'
                GROUP BY agent_id{validator_set_columns}
            )
        SELECT
            agents.agent_id,
            agents.status,
            COALESCE(validator_eval_counts.num_running_evals, 0) AS num_running_evals,
            COALESCE(validator_eval_counts.num_finished_evals, 0) AS num_finished_evals
        FROM agents
        {competition_join}
        INNER JOIN screener_2_scores
            ON screener_2_scores.agent_id = agents.agent_id
            {screener_set_join}
        LEFT JOIN validator_eval_counts
            ON validator_eval_counts.agent_id = agents.agent_id
            {validator_set_join}
        WHERE agents.status = 'evaluating'
          {competition_predicate}
          AND COALESCE(validator_eval_counts.num_running_evals, 0)
              + COALESCE(validator_eval_counts.num_finished_evals, 0) < {validator_limit}
          AND agents.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
          AND NOT EXISTS (
            SELECT 1
            FROM banned_coldkeys
            WHERE banned_coldkeys.miner_coldkey = agents.miner_coldkey
          )
          AND agents.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
        ORDER BY
            screener_2_scores.score DESC,
            agents.created_at ASC,
            num_finished_evals DESC;
    """)


def upgrade() -> None:
    cursor_table = op.create_table(
        "competition_work_cursors",
        sa.Column("family", sa.Text(), nullable=False),
        sa.Column("last_served_set_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "family IN ('screener_1', 'screener_2', 'validator', 'pre_screening_judge', 'approval_judge')",
            name="ck_competition_work_cursors_family",
        ),
        sa.PrimaryKeyConstraint("family"),
    )
    op.bulk_insert(
        cursor_table,
        [
            {"family": "screener_1", "last_served_set_id": None},
            {"family": "screener_2", "last_served_set_id": None},
            {"family": "validator", "last_served_set_id": None},
            {"family": "pre_screening_judge", "last_served_set_id": None},
            {"family": "approval_judge", "last_served_set_id": None},
        ],
    )
    _replace_queue_views(competition_scoped=True)


def downgrade() -> None:
    _replace_queue_views(competition_scoped=False)
    op.drop_table("competition_work_cursors")
