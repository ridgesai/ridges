"""score future sets by validator consensus per problem

Revision ID: c7d4e9a1b2f3
Revises: 234ed0606f2a
Create Date: 2026-05-07 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c7d4e9a1b2f3"
down_revision: Union[str, None] = "234ed0606f2a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _get_cutoff_set_id() -> int:
    result = op.get_bind().execute(sa.text("SELECT COALESCE(MAX(set_id), 0) FROM evaluation_sets")).scalar_one()
    return int(result)


def _refresh_agent_scores_consensus(cutoff_set_id: int) -> str:
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
          AND a.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
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


def _populate_agent_scores_consensus(cutoff_set_id: int) -> str:
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
        WHERE miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
          AND agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
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


REFRESH_AGENT_SCORES_AVERAGE = """
CREATE OR REPLACE FUNCTION refresh_agent_scores_for_agent(target_agent_id UUID)
RETURNS VOID AS $$
BEGIN
    DELETE FROM agent_scores WHERE agent_id = target_agent_id;
    INSERT INTO agent_scores (
        agent_id, miner_hotkey, name, version_num, created_at, status,
        set_id, approved, approved_at, validator_count, final_score
    )
    WITH agent_evaluations AS (
        SELECT
            a.agent_id, a.miner_hotkey, a.name, a.version_num, a.created_at, a.status,
            e.set_id, e.score, e.validator_hotkey, e.evaluation_set_group,
            (avi.agent_id IS NOT NULL AND avi.approved_at <= NOW()) as approved,
            avi.approved_at
        FROM agents a
        INNER JOIN evaluations_hydrated e ON a.agent_id = e.agent_id
            AND e.status = 'success'
            AND e.score IS NOT NULL
            AND e.score > 0
            AND e.evaluation_set_group = 'validator'::evaluationsetgroup
            AND e.set_id IS NOT NULL
        LEFT JOIN approved_agents avi ON a.agent_id = avi.agent_id AND e.set_id = avi.set_id
        WHERE a.agent_id = target_agent_id
          AND a.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
          AND a.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
    )
    SELECT
        ae.agent_id, ae.miner_hotkey, ae.name, ae.version_num, ae.created_at, ae.status,
        ae.set_id, ae.approved, ae.approved_at,
        COUNT(DISTINCT ae.validator_hotkey) AS validator_count,
        AVG(ae.score) AS final_score
    FROM agent_evaluations ae
    WHERE ae.set_id IS NOT NULL
    GROUP BY ae.agent_id, ae.miner_hotkey, ae.name, ae.version_num,
             ae.created_at, ae.status, ae.set_id, ae.approved, ae.approved_at
    HAVING COUNT(DISTINCT ae.validator_hotkey) >= 2;
END;
$$ LANGUAGE plpgsql;
"""


POPULATE_AGENT_SCORES_AVERAGE = """
CREATE OR REPLACE FUNCTION populate_agent_scores()
RETURNS VOID AS $$
BEGIN
    TRUNCATE TABLE agent_scores;
    INSERT INTO agent_scores (
        agent_id, miner_hotkey, name, version_num, created_at, status,
        set_id, approved, approved_at, validator_count, final_score
    )
    WITH all_agents AS (
        SELECT agent_id, miner_hotkey, name, version_num, created_at, status
        FROM agents
        WHERE miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
          AND agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
    ),
    agent_evaluations AS (
        SELECT
            aa.agent_id, aa.miner_hotkey, aa.name, aa.version_num, aa.created_at, aa.status,
            e.set_id, e.score, e.validator_hotkey,
            (avi.agent_id IS NOT NULL AND avi.approved_at <= NOW()) as approved,
            avi.approved_at
        FROM all_agents aa
        INNER JOIN evaluations_hydrated e ON aa.agent_id = e.agent_id
            AND e.status = 'success'
            AND e.score IS NOT NULL
            AND e.score > 0
            AND e.evaluation_set_group = 'validator'::evaluationsetgroup
            AND e.set_id IS NOT NULL
        LEFT JOIN approved_agents avi ON aa.agent_id = avi.agent_id AND e.set_id = avi.set_id
    )
    SELECT
        ae.agent_id, ae.miner_hotkey, ae.name, ae.version_num, ae.created_at, ae.status,
        ae.set_id, ae.approved, ae.approved_at,
        COUNT(DISTINCT ae.validator_hotkey) AS validator_count,
        AVG(ae.score) AS final_score
    FROM agent_evaluations ae
    WHERE ae.set_id IS NOT NULL
    GROUP BY ae.agent_id, ae.miner_hotkey, ae.name, ae.version_num,
             ae.created_at, ae.status, ae.set_id, ae.approved, ae.approved_at
    HAVING COUNT(DISTINCT ae.validator_hotkey) >= 2;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    cutoff_set_id = _get_cutoff_set_id()
    op.execute(_refresh_agent_scores_consensus(cutoff_set_id))
    op.execute(_populate_agent_scores_consensus(cutoff_set_id))


def downgrade() -> None:
    op.execute(REFRESH_AGENT_SCORES_AVERAGE)
    op.execute(POPULATE_AGENT_SCORES_AVERAGE)
    op.execute("SELECT populate_agent_scores();")
