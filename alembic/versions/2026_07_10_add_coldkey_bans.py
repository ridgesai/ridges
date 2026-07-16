"""Add coldkey bans and agent coldkey provenance.

Revision ID: b7e4d2c9a106
Revises: c8f41e9a2b37
Create Date: 2026-07-10 00:00:00.000000

"""

import re
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "b7e4d2c9a106"
down_revision: Union[str, Sequence[str], None] = "c8f41e9a2b37"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_HOTKEY_BAN_CONDITION = "agents.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)"
_COLDKEY_BAN_CONDITION = """NOT EXISTS (
            SELECT 1
            FROM banned_coldkeys
            WHERE banned_coldkeys.miner_coldkey = agents.miner_coldkey
          )"""


def _replace_queue_views(ban_condition: str) -> None:
    op.execute(f"""
        CREATE OR REPLACE VIEW pre_screening_queue AS
        SELECT agents.agent_id, agents.status
        FROM agents
        WHERE agents.status IN ('pre_screening', 'pre_screening_needs_review')
          AND agents.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
          AND {ban_condition}
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
            WHERE agents.status = '{status}'
              AND NOT EXISTS (
                SELECT 1 FROM evaluations e
                WHERE e.agent_id = agents.agent_id
                  AND e.evaluation_set_group = '{set_group}'::evaluationsetgroup
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
              AND {ban_condition}
              AND agents.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
            ORDER BY agents.created_at ASC;
        """)

    op.execute(f"""
        CREATE OR REPLACE VIEW validator_queue AS
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
            AND {ban_condition}
            AND agents.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
        ORDER BY
            screener_2_scores.score DESC,
            agents.created_at ASC,
            num_finished_evals DESC;
    """)


def _live_cutoff_set_id() -> int:
    definition = (
        op.get_bind()
        .execute(sa.text("SELECT pg_get_functiondef(to_regprocedure('populate_agent_scores()'))"))
        .scalar_one()
    )
    if definition is None:
        raise RuntimeError("Missing function populate_agent_scores()")
    cutoffs = {int(match) for match in re.findall(r"set_id > (\d+)", definition)}
    if len(cutoffs) != 1:
        raise RuntimeError(f"Could not determine the consensus cutoff from populate_agent_scores():\n{definition}")
    return cutoffs.pop()


def _banned_hotkeys_trigger_branch(row: str, include_hotkey_bans: bool) -> str:
    if not include_hotkey_bans:
        return ""
    return (
        f"                ELSIF TG_TABLE_NAME = 'banned_hotkeys' THEN\n"
        f"                    DELETE FROM agent_scores\n"
        f"                    WHERE agent_id IN (SELECT agent_id FROM agents WHERE miner_hotkey = {row}.miner_hotkey);\n"
        f"                    RETURN {row};\n"
    )


def _refresh_agent_scores_trigger_sql(include_hotkey_bans: bool) -> str:
    old_branch = _banned_hotkeys_trigger_branch("OLD", include_hotkey_bans)
    new_branch = _banned_hotkeys_trigger_branch("NEW", include_hotkey_bans)
    return f"""
        CREATE OR REPLACE FUNCTION refresh_agent_scores()
        RETURNS TRIGGER AS $$
        DECLARE
            affected_agent_id UUID;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF TG_TABLE_NAME = 'evaluations' THEN
                    affected_agent_id := OLD.agent_id;
                ELSIF TG_TABLE_NAME = 'agents' THEN
                    DELETE FROM agent_scores WHERE agent_id = OLD.agent_id;
                    RETURN OLD;
                ELSIF TG_TABLE_NAME = 'approved_agents' THEN
                    affected_agent_id := OLD.agent_id;
{old_branch}                ELSIF TG_TABLE_NAME = 'unapproved_agent_ids' THEN
                    affected_agent_id := OLD.agent_id;
                END IF;
            ELSIF TG_OP = 'TRUNCATE' THEN
                PERFORM populate_agent_scores();
                RETURN NULL;
            ELSE
                IF TG_TABLE_NAME = 'evaluations' THEN
                    affected_agent_id := NEW.agent_id;
                ELSIF TG_TABLE_NAME = 'agents' THEN
                    affected_agent_id := NEW.agent_id;
                ELSIF TG_TABLE_NAME = 'approved_agents' THEN
                    affected_agent_id := NEW.agent_id;
{new_branch}                ELSIF TG_TABLE_NAME = 'unapproved_agent_ids' THEN
                    DELETE FROM agent_scores WHERE agent_id = NEW.agent_id;
                    RETURN NEW;
                END IF;
            END IF;

            IF affected_agent_id IS NOT NULL THEN
                PERFORM refresh_agent_scores_for_agent(affected_agent_id);
            END IF;

            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            ELSE
                RETURN NEW;
            END IF;
        END;
        $$ LANGUAGE plpgsql;
    """


def _refresh_agent_scores_for_agent_sql(cutoff_set_id: int, include_hotkey_bans: bool) -> str:
    hotkey_filter = (
        "          AND a.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)\n" if include_hotkey_bans else ""
    )
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
{hotkey_filter}          AND a.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
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


def _populate_agent_scores_sql(cutoff_set_id: int, include_hotkey_bans: bool) -> str:
    all_agents_filter = (
        "        WHERE miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)\n"
        "          AND agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)"
        if include_hotkey_bans
        else "        WHERE agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)"
    )
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
{all_agents_filter}
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


def _replace_agent_score_functions(include_hotkey_bans: bool) -> None:
    cutoff_set_id = _live_cutoff_set_id()
    op.execute(_refresh_agent_scores_for_agent_sql(cutoff_set_id, include_hotkey_bans))
    op.execute(_populate_agent_scores_sql(cutoff_set_id, include_hotkey_bans))
    op.execute(_refresh_agent_scores_trigger_sql(include_hotkey_bans))


def upgrade() -> None:
    op.create_table(
        "banned_coldkeys",
        sa.Column("miner_coldkey", sa.Text(), primary_key=True),
        sa.Column("banned_reason", sa.Text(), nullable=False),
        sa.Column(
            "banned_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
    )

    op.add_column("agents", sa.Column("miner_coldkey", sa.Text(), nullable=True))

    _replace_queue_views(_COLDKEY_BAN_CONDITION)
    _replace_agent_score_functions(include_hotkey_bans=False)
    op.execute("DROP TRIGGER IF EXISTS tr_refresh_agent_scores_banned_hotkeys ON banned_hotkeys;")


def downgrade() -> None:
    _replace_agent_score_functions(include_hotkey_bans=True)
    op.execute("""
        CREATE TRIGGER tr_refresh_agent_scores_banned_hotkeys
        AFTER INSERT OR UPDATE OR DELETE ON banned_hotkeys
        FOR EACH ROW EXECUTE PROCEDURE refresh_agent_scores();
    """)
    _replace_queue_views(_HOTKEY_BAN_CONDITION)

    op.drop_column("agents", "miner_coldkey")
    op.drop_table("banned_coldkeys")
