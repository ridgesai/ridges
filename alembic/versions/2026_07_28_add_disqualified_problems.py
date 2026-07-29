"""Add disqualified_problems and problem_disqualification_jobs; exclude disqualified problems from scoring.

Revision ID: a1b2c3d4e5f6
Revises: e5c8a1f0b942
Create Date: 2026-07-28 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "e5c8a1f0b942"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _get_cutoff_set_id() -> int:
    result = op.get_bind().execute(sa.text("SELECT COALESCE(MAX(set_id), 0) FROM evaluation_sets")).scalar_one()
    return int(result)


# --- Scoring functions WITH the disqualified-problem exclusion.
#
# NOTE: these are based on the functions as they exist at HEAD (revision e5c8a1f0b942), i.e. the
# shape produced by alembic/versions/2026_07_10_add_coldkey_bans.py::_refresh_agent_scores_for_agent_sql /
# _populate_agent_scores_sql with include_hotkey_bans=False (the `banned_hotkeys` filter on
# eligible_agents/all_agents was removed in favor of coldkey bans and must NOT be reintroduced here).
# The only additions relative to that HEAD shape are the two `NOT EXISTS disqualified_problems`
# filters marked <<< DQ EXCLUSION.
def _refresh_agent_scores_consensus_with_dq(cutoff_set_id: int) -> str:
    return f"""
CREATE OR REPLACE FUNCTION refresh_agent_scores_for_agent(target_agent_id UUID)
RETURNS VOID AS $$
BEGIN
    DELETE FROM agent_scores
    WHERE agent_id = target_agent_id
      AND set_id > {cutoff_set_id};

    INSERT INTO agent_scores (
        agent_id, miner_hotkey, name, version_num, created_at, status,
        set_id, approved, approved_at, validator_count, final_score
    )
    WITH eligible_agents AS (
        SELECT a.agent_id, a.miner_hotkey, a.name, a.version_num, a.created_at, a.status
        FROM agents a
        WHERE a.agent_id = target_agent_id
          AND a.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
    ),
    validator_evaluations AS (
        SELECT
            ea.agent_id, ea.miner_hotkey, ea.name, ea.version_num, ea.created_at, ea.status,
            e.evaluation_id, e.set_id, e.validator_hotkey,
            (avi.agent_id IS NOT NULL AND avi.approved_at <= NOW()) AS approved,
            avi.approved_at
        FROM eligible_agents ea
        INNER JOIN evaluations_hydrated e ON ea.agent_id = e.agent_id
            AND e.status = 'success'
            AND e.evaluation_set_group = 'validator'::evaluationsetgroup
            AND e.set_id > {cutoff_set_id}
        LEFT JOIN approved_agents avi ON ea.agent_id = avi.agent_id AND e.set_id = avi.set_id
    ),
    validator_counts AS (
        SELECT
            agent_id, miner_hotkey, name, version_num, created_at, status, set_id,
            BOOL_OR(approved) AS approved,
            MAX(approved_at) AS approved_at,
            COUNT(DISTINCT validator_hotkey) AS validator_count
        FROM validator_evaluations
        GROUP BY agent_id, miner_hotkey, name, version_num, created_at, status, set_id
    ),
    set_problem_counts AS (
        SELECT set_id, COUNT(*) AS problem_count
        FROM evaluation_sets es
        WHERE set_group = 'validator'::evaluationsetgroup
          AND set_id > {cutoff_set_id}
          AND NOT EXISTS (                                            -- <<< DQ EXCLUSION (denominator)
              SELECT 1 FROM disqualified_problems dp
              WHERE dp.set_id = es.set_id
                AND dp.set_group = es.set_group
                AND dp.problem_name = es.problem_name
          )
        GROUP BY set_id
    ),
    consensus_by_problem AS (
        SELECT
            ve.agent_id,
            ve.set_id,
            es.problem_name,
            COUNT(DISTINCT ve.validator_hotkey) FILTER (WHERE erh.solved IS TRUE) AS solved_validator_count
        FROM validator_evaluations ve
        INNER JOIN evaluation_runs_hydrated erh ON erh.evaluation_id = ve.evaluation_id
        INNER JOIN evaluation_sets es ON es.set_id = ve.set_id
            AND es.set_group = 'validator'::evaluationsetgroup
            AND es.problem_name = erh.problem_name
            AND NOT EXISTS (                                          -- <<< DQ EXCLUSION (numerator)
                SELECT 1 FROM disqualified_problems dp
                WHERE dp.set_id = es.set_id
                  AND dp.set_group = es.set_group
                  AND dp.problem_name = es.problem_name
            )
        GROUP BY ve.agent_id, ve.set_id, es.problem_name
    ),
    consensus_scores AS (
        SELECT
            vc.agent_id,
            vc.miner_hotkey,
            vc.name,
            vc.version_num,
            vc.created_at,
            vc.status,
            vc.set_id,
            vc.approved,
            vc.approved_at,
            vc.validator_count::int AS validator_count,
            (
                COUNT(*) FILTER (WHERE cbp.solved_validator_count = vc.validator_count)::float
                / spc.problem_count
            ) AS final_score
        FROM validator_counts vc
        INNER JOIN set_problem_counts spc ON spc.set_id = vc.set_id
        INNER JOIN consensus_by_problem cbp ON cbp.agent_id = vc.agent_id AND cbp.set_id = vc.set_id
        WHERE spc.problem_count > 0
        GROUP BY
            vc.agent_id, vc.miner_hotkey, vc.name, vc.version_num, vc.created_at, vc.status,
            vc.set_id, vc.approved, vc.approved_at, vc.validator_count, spc.problem_count
        HAVING
            vc.validator_count >= 2
            AND COUNT(*) FILTER (WHERE cbp.solved_validator_count = vc.validator_count) > 0
    ),
    ranked_scores AS (
        SELECT
            consensus_scores.*,
            ROW_NUMBER() OVER (PARTITION BY agent_id ORDER BY set_id DESC) AS score_rank
        FROM consensus_scores
    )
    SELECT
        agent_id, miner_hotkey, name, version_num, created_at, status,
        set_id, approved, approved_at, validator_count, final_score
    FROM ranked_scores
    WHERE score_rank = 1
    ON CONFLICT (agent_id) DO UPDATE SET
        miner_hotkey = EXCLUDED.miner_hotkey,
        name = EXCLUDED.name,
        version_num = EXCLUDED.version_num,
        created_at = EXCLUDED.created_at,
        status = EXCLUDED.status,
        set_id = EXCLUDED.set_id,
        approved = EXCLUDED.approved,
        approved_at = EXCLUDED.approved_at,
        validator_count = EXCLUDED.validator_count,
        final_score = EXCLUDED.final_score;
END;
$$ LANGUAGE plpgsql;
"""


def _populate_agent_scores_consensus_with_dq(cutoff_set_id: int) -> str:
    return f"""
CREATE OR REPLACE FUNCTION populate_agent_scores()
RETURNS VOID AS $$
BEGIN
    DELETE FROM agent_scores
    WHERE set_id > {cutoff_set_id};

    INSERT INTO agent_scores (
        agent_id, miner_hotkey, name, version_num, created_at, status,
        set_id, approved, approved_at, validator_count, final_score
    )
    WITH all_agents AS (
        SELECT agent_id, miner_hotkey, name, version_num, created_at, status
        FROM agents
        WHERE agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
    ),
    validator_evaluations AS (
        SELECT
            aa.agent_id, aa.miner_hotkey, aa.name, aa.version_num, aa.created_at, aa.status,
            e.evaluation_id, e.set_id, e.validator_hotkey,
            (avi.agent_id IS NOT NULL AND avi.approved_at <= NOW()) AS approved,
            avi.approved_at
        FROM all_agents aa
        INNER JOIN evaluations_hydrated e ON aa.agent_id = e.agent_id
            AND e.status = 'success'
            AND e.evaluation_set_group = 'validator'::evaluationsetgroup
            AND e.set_id > {cutoff_set_id}
        LEFT JOIN approved_agents avi ON aa.agent_id = avi.agent_id AND e.set_id = avi.set_id
    ),
    validator_counts AS (
        SELECT
            agent_id, miner_hotkey, name, version_num, created_at, status, set_id,
            BOOL_OR(approved) AS approved,
            MAX(approved_at) AS approved_at,
            COUNT(DISTINCT validator_hotkey) AS validator_count
        FROM validator_evaluations
        GROUP BY agent_id, miner_hotkey, name, version_num, created_at, status, set_id
    ),
    set_problem_counts AS (
        SELECT set_id, COUNT(*) AS problem_count
        FROM evaluation_sets es
        WHERE set_group = 'validator'::evaluationsetgroup
          AND set_id > {cutoff_set_id}
          AND NOT EXISTS (                                            -- <<< DQ EXCLUSION (denominator)
              SELECT 1 FROM disqualified_problems dp
              WHERE dp.set_id = es.set_id
                AND dp.set_group = es.set_group
                AND dp.problem_name = es.problem_name
          )
        GROUP BY set_id
    ),
    consensus_by_problem AS (
        SELECT
            ve.agent_id,
            ve.set_id,
            es.problem_name,
            COUNT(DISTINCT ve.validator_hotkey) FILTER (WHERE erh.solved IS TRUE) AS solved_validator_count
        FROM validator_evaluations ve
        INNER JOIN evaluation_runs_hydrated erh ON erh.evaluation_id = ve.evaluation_id
        INNER JOIN evaluation_sets es ON es.set_id = ve.set_id
            AND es.set_group = 'validator'::evaluationsetgroup
            AND es.problem_name = erh.problem_name
            AND NOT EXISTS (                                          -- <<< DQ EXCLUSION (numerator)
                SELECT 1 FROM disqualified_problems dp
                WHERE dp.set_id = es.set_id
                  AND dp.set_group = es.set_group
                  AND dp.problem_name = es.problem_name
            )
        GROUP BY ve.agent_id, ve.set_id, es.problem_name
    ),
    consensus_scores AS (
        SELECT
            vc.agent_id,
            vc.miner_hotkey,
            vc.name,
            vc.version_num,
            vc.created_at,
            vc.status,
            vc.set_id,
            vc.approved,
            vc.approved_at,
            vc.validator_count::int AS validator_count,
            (
                COUNT(*) FILTER (WHERE cbp.solved_validator_count = vc.validator_count)::float
                / spc.problem_count
            ) AS final_score
        FROM validator_counts vc
        INNER JOIN set_problem_counts spc ON spc.set_id = vc.set_id
        INNER JOIN consensus_by_problem cbp ON cbp.agent_id = vc.agent_id AND cbp.set_id = vc.set_id
        WHERE spc.problem_count > 0
        GROUP BY
            vc.agent_id, vc.miner_hotkey, vc.name, vc.version_num, vc.created_at, vc.status,
            vc.set_id, vc.approved, vc.approved_at, vc.validator_count, spc.problem_count
        HAVING
            vc.validator_count >= 2
            AND COUNT(*) FILTER (WHERE cbp.solved_validator_count = vc.validator_count) > 0
    ),
    ranked_scores AS (
        SELECT
            consensus_scores.*,
            ROW_NUMBER() OVER (PARTITION BY agent_id ORDER BY set_id DESC) AS score_rank
        FROM consensus_scores
    )
    SELECT
        agent_id, miner_hotkey, name, version_num, created_at, status,
        set_id, approved, approved_at, validator_count, final_score
    FROM ranked_scores
    WHERE score_rank = 1
    ON CONFLICT (agent_id) DO UPDATE SET
        miner_hotkey = EXCLUDED.miner_hotkey,
        name = EXCLUDED.name,
        version_num = EXCLUDED.version_num,
        created_at = EXCLUDED.created_at,
        status = EXCLUDED.status,
        set_id = EXCLUDED.set_id,
        approved = EXCLUDED.approved,
        approved_at = EXCLUDED.approved_at,
        validator_count = EXCLUDED.validator_count,
        final_score = EXCLUDED.final_score;
END;
$$ LANGUAGE plpgsql;
"""


# --- Scoring functions WITHOUT the disqualified-problem exclusion (the pre-DQ shape).
#
# These are the exact bodies live at down_revision (e5c8a1f0b942), i.e. what
# alembic/versions/2026_07_10_add_coldkey_bans.py installed via
# _refresh_agent_scores_for_agent_sql(cutoff, include_hotkey_bans=False) /
# _populate_agent_scores_sql(cutoff, include_hotkey_bans=False), verified by inspecting
# pg_get_functiondef() against a database freshly migrated to head. downgrade() restores these
# verbatim (self-contained — no cross-module import of the 2026_05_07 migration, since that
# module's filename starts with a digit and isn't importable that way, and its bodies are stale
# relative to HEAD regardless — they still contain the banned_hotkeys filter that coldkey-bans
# removed).
def _refresh_agent_scores_consensus_without_dq(cutoff_set_id: int) -> str:
    return f"""
CREATE OR REPLACE FUNCTION refresh_agent_scores_for_agent(target_agent_id UUID)
RETURNS VOID AS $$
BEGIN
    DELETE FROM agent_scores
    WHERE agent_id = target_agent_id
      AND set_id > {cutoff_set_id};

    INSERT INTO agent_scores (
        agent_id, miner_hotkey, name, version_num, created_at, status,
        set_id, approved, approved_at, validator_count, final_score
    )
    WITH eligible_agents AS (
        SELECT a.agent_id, a.miner_hotkey, a.name, a.version_num, a.created_at, a.status
        FROM agents a
        WHERE a.agent_id = target_agent_id
          AND a.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
    ),
    validator_evaluations AS (
        SELECT
            ea.agent_id, ea.miner_hotkey, ea.name, ea.version_num, ea.created_at, ea.status,
            e.evaluation_id, e.set_id, e.validator_hotkey,
            (avi.agent_id IS NOT NULL AND avi.approved_at <= NOW()) AS approved,
            avi.approved_at
        FROM eligible_agents ea
        INNER JOIN evaluations_hydrated e ON ea.agent_id = e.agent_id
            AND e.status = 'success'
            AND e.evaluation_set_group = 'validator'::evaluationsetgroup
            AND e.set_id > {cutoff_set_id}
        LEFT JOIN approved_agents avi ON ea.agent_id = avi.agent_id AND e.set_id = avi.set_id
    ),
    validator_counts AS (
        SELECT
            agent_id, miner_hotkey, name, version_num, created_at, status, set_id,
            BOOL_OR(approved) AS approved,
            MAX(approved_at) AS approved_at,
            COUNT(DISTINCT validator_hotkey) AS validator_count
        FROM validator_evaluations
        GROUP BY agent_id, miner_hotkey, name, version_num, created_at, status, set_id
    ),
    set_problem_counts AS (
        SELECT set_id, COUNT(*) AS problem_count
        FROM evaluation_sets
        WHERE set_group = 'validator'::evaluationsetgroup
          AND set_id > {cutoff_set_id}
        GROUP BY set_id
    ),
    consensus_by_problem AS (
        SELECT
            ve.agent_id,
            ve.set_id,
            es.problem_name,
            COUNT(DISTINCT ve.validator_hotkey) FILTER (WHERE erh.solved IS TRUE) AS solved_validator_count
        FROM validator_evaluations ve
        INNER JOIN evaluation_runs_hydrated erh ON erh.evaluation_id = ve.evaluation_id
        INNER JOIN evaluation_sets es ON es.set_id = ve.set_id
            AND es.set_group = 'validator'::evaluationsetgroup
            AND es.problem_name = erh.problem_name
        GROUP BY ve.agent_id, ve.set_id, es.problem_name
    ),
    consensus_scores AS (
        SELECT
            vc.agent_id,
            vc.miner_hotkey,
            vc.name,
            vc.version_num,
            vc.created_at,
            vc.status,
            vc.set_id,
            vc.approved,
            vc.approved_at,
            vc.validator_count::int AS validator_count,
            (
                COUNT(*) FILTER (WHERE cbp.solved_validator_count = vc.validator_count)::float
                / spc.problem_count
            ) AS final_score
        FROM validator_counts vc
        INNER JOIN set_problem_counts spc ON spc.set_id = vc.set_id
        INNER JOIN consensus_by_problem cbp ON cbp.agent_id = vc.agent_id AND cbp.set_id = vc.set_id
        WHERE spc.problem_count > 0
        GROUP BY
            vc.agent_id, vc.miner_hotkey, vc.name, vc.version_num, vc.created_at, vc.status,
            vc.set_id, vc.approved, vc.approved_at, vc.validator_count, spc.problem_count
        HAVING
            vc.validator_count >= 2
            AND COUNT(*) FILTER (WHERE cbp.solved_validator_count = vc.validator_count) > 0
    ),
    ranked_scores AS (
        SELECT
            consensus_scores.*,
            ROW_NUMBER() OVER (PARTITION BY agent_id ORDER BY set_id DESC) AS score_rank
        FROM consensus_scores
    )
    SELECT
        agent_id, miner_hotkey, name, version_num, created_at, status,
        set_id, approved, approved_at, validator_count, final_score
    FROM ranked_scores
    WHERE score_rank = 1
    ON CONFLICT (agent_id) DO UPDATE SET
        miner_hotkey = EXCLUDED.miner_hotkey,
        name = EXCLUDED.name,
        version_num = EXCLUDED.version_num,
        created_at = EXCLUDED.created_at,
        status = EXCLUDED.status,
        set_id = EXCLUDED.set_id,
        approved = EXCLUDED.approved,
        approved_at = EXCLUDED.approved_at,
        validator_count = EXCLUDED.validator_count,
        final_score = EXCLUDED.final_score;
END;
$$ LANGUAGE plpgsql;
"""


def _populate_agent_scores_consensus_without_dq(cutoff_set_id: int) -> str:
    return f"""
CREATE OR REPLACE FUNCTION populate_agent_scores()
RETURNS VOID AS $$
BEGIN
    DELETE FROM agent_scores
    WHERE set_id > {cutoff_set_id};

    INSERT INTO agent_scores (
        agent_id, miner_hotkey, name, version_num, created_at, status,
        set_id, approved, approved_at, validator_count, final_score
    )
    WITH all_agents AS (
        SELECT agent_id, miner_hotkey, name, version_num, created_at, status
        FROM agents
        WHERE agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
    ),
    validator_evaluations AS (
        SELECT
            aa.agent_id, aa.miner_hotkey, aa.name, aa.version_num, aa.created_at, aa.status,
            e.evaluation_id, e.set_id, e.validator_hotkey,
            (avi.agent_id IS NOT NULL AND avi.approved_at <= NOW()) AS approved,
            avi.approved_at
        FROM all_agents aa
        INNER JOIN evaluations_hydrated e ON aa.agent_id = e.agent_id
            AND e.status = 'success'
            AND e.evaluation_set_group = 'validator'::evaluationsetgroup
            AND e.set_id > {cutoff_set_id}
        LEFT JOIN approved_agents avi ON aa.agent_id = avi.agent_id AND e.set_id = avi.set_id
    ),
    validator_counts AS (
        SELECT
            agent_id, miner_hotkey, name, version_num, created_at, status, set_id,
            BOOL_OR(approved) AS approved,
            MAX(approved_at) AS approved_at,
            COUNT(DISTINCT validator_hotkey) AS validator_count
        FROM validator_evaluations
        GROUP BY agent_id, miner_hotkey, name, version_num, created_at, status, set_id
    ),
    set_problem_counts AS (
        SELECT set_id, COUNT(*) AS problem_count
        FROM evaluation_sets
        WHERE set_group = 'validator'::evaluationsetgroup
          AND set_id > {cutoff_set_id}
        GROUP BY set_id
    ),
    consensus_by_problem AS (
        SELECT
            ve.agent_id,
            ve.set_id,
            es.problem_name,
            COUNT(DISTINCT ve.validator_hotkey) FILTER (WHERE erh.solved IS TRUE) AS solved_validator_count
        FROM validator_evaluations ve
        INNER JOIN evaluation_runs_hydrated erh ON erh.evaluation_id = ve.evaluation_id
        INNER JOIN evaluation_sets es ON es.set_id = ve.set_id
            AND es.set_group = 'validator'::evaluationsetgroup
            AND es.problem_name = erh.problem_name
        GROUP BY ve.agent_id, ve.set_id, es.problem_name
    ),
    consensus_scores AS (
        SELECT
            vc.agent_id,
            vc.miner_hotkey,
            vc.name,
            vc.version_num,
            vc.created_at,
            vc.status,
            vc.set_id,
            vc.approved,
            vc.approved_at,
            vc.validator_count::int AS validator_count,
            (
                COUNT(*) FILTER (WHERE cbp.solved_validator_count = vc.validator_count)::float
                / spc.problem_count
            ) AS final_score
        FROM validator_counts vc
        INNER JOIN set_problem_counts spc ON spc.set_id = vc.set_id
        INNER JOIN consensus_by_problem cbp ON cbp.agent_id = vc.agent_id AND cbp.set_id = vc.set_id
        WHERE spc.problem_count > 0
        GROUP BY
            vc.agent_id, vc.miner_hotkey, vc.name, vc.version_num, vc.created_at, vc.status,
            vc.set_id, vc.approved, vc.approved_at, vc.validator_count, spc.problem_count
        HAVING
            vc.validator_count >= 2
            AND COUNT(*) FILTER (WHERE cbp.solved_validator_count = vc.validator_count) > 0
    ),
    ranked_scores AS (
        SELECT
            consensus_scores.*,
            ROW_NUMBER() OVER (PARTITION BY agent_id ORDER BY set_id DESC) AS score_rank
        FROM consensus_scores
    )
    SELECT
        agent_id, miner_hotkey, name, version_num, created_at, status,
        set_id, approved, approved_at, validator_count, final_score
    FROM ranked_scores
    WHERE score_rank = 1
    ON CONFLICT (agent_id) DO UPDATE SET
        miner_hotkey = EXCLUDED.miner_hotkey,
        name = EXCLUDED.name,
        version_num = EXCLUDED.version_num,
        created_at = EXCLUDED.created_at,
        status = EXCLUDED.status,
        set_id = EXCLUDED.set_id,
        approved = EXCLUDED.approved,
        approved_at = EXCLUDED.approved_at,
        validator_count = EXCLUDED.validator_count,
        final_score = EXCLUDED.final_score;
END;
$$ LANGUAGE plpgsql;
"""


def _restore_functions_without_dq() -> None:
    cutoff_set_id = _get_cutoff_set_id()
    op.execute(_refresh_agent_scores_consensus_without_dq(cutoff_set_id))
    op.execute(_populate_agent_scores_consensus_without_dq(cutoff_set_id))


def upgrade() -> None:
    op.create_table(
        "disqualified_problems",
        sa.Column("set_id", sa.Integer(), nullable=False),
        sa.Column(
            "set_group",
            postgresql.ENUM(name="evaluationsetgroup", create_type=False),
            nullable=False,
        ),
        sa.Column("problem_name", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.PrimaryKeyConstraint("set_id", "set_group", "problem_name"),
        sa.ForeignKeyConstraint(
            ["set_id", "set_group", "problem_name"],
            ["evaluation_sets.set_id", "evaluation_sets.set_group", "evaluation_sets.problem_name"],
            ondelete="CASCADE",
        ),
    )

    op.create_table(
        "problem_disqualification_jobs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("set_id", sa.Integer(), nullable=False),
        sa.Column(
            "set_group",
            postgresql.ENUM(name="evaluationsetgroup", create_type=False),
            nullable=False,
        ),
        sa.Column("problem_name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("processed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index(
        "uq_problem_disqualification_jobs_pending",
        "problem_disqualification_jobs",
        ["set_id", "set_group", "problem_name"],
        unique=True,
        postgresql_where=sa.text("processed_at IS NULL"),
    )

    cutoff_set_id = _get_cutoff_set_id()
    op.execute(_refresh_agent_scores_consensus_with_dq(cutoff_set_id))
    op.execute(_populate_agent_scores_consensus_with_dq(cutoff_set_id))


def downgrade() -> None:
    _restore_functions_without_dq()
    op.drop_index("uq_problem_disqualification_jobs_pending", table_name="problem_disqualification_jobs")
    op.drop_table("problem_disqualification_jobs")
    op.drop_table("disqualified_problems")
