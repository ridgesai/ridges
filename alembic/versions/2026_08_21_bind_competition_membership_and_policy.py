"""Bind operational rows and scoring to an agent's competition.

Revision ID: 07af81b81a3e
Revises: 6f59f4e0c487
Create Date: 2026-08-21 00:35:11.050805

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "07af81b81a3e"
down_revision: Union[str, Sequence[str], None] = "6f59f4e0c487"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_OLD_REFRESH_FUNCTION = "refresh_agent_scores_for_agent_pre_multi_competition"
_OLD_POPULATE_FUNCTION = "populate_agent_scores_pre_multi_competition"


REFRESH_AGENT_SCORES_FOR_AGENT = """
CREATE FUNCTION refresh_agent_scores_for_agent(target_agent_id UUID)
RETURNS VOID AS $$
DECLARE
    target_set_id INTEGER;
    target_scoring_mode TEXT;
BEGIN
    SELECT a.set_id, c.scoring_mode
    INTO target_set_id, target_scoring_mode
    FROM agents a
    INNER JOIN competitions c ON c.set_id = a.set_id
    WHERE a.agent_id = target_agent_id
      AND a.set_id IS NOT NULL
      AND c.scoring_mode IS NOT NULL;

    -- Preserve legacy or uninitialized rows until they have explicit membership and policy.
    IF target_set_id IS NULL OR target_scoring_mode IS NULL THEN
        RETURN;
    END IF;

    DELETE FROM agent_scores WHERE agent_id = target_agent_id;

    IF target_agent_id IN (SELECT agent_id FROM unapproved_agent_ids) THEN
        RETURN;
    END IF;

    IF target_scoring_mode = 'legacy' THEN
        INSERT INTO agent_scores (
            agent_id, miner_hotkey, name, version_num, created_at, status,
            set_id, approved, approved_at, validator_count, final_score
        )
        WITH agent_evaluations AS (
            SELECT
                a.agent_id,
                a.miner_hotkey,
                a.name,
                a.version_num,
                a.created_at,
                a.status,
                e.set_id,
                e.score,
                e.validator_hotkey,
                (avi.agent_id IS NOT NULL AND avi.approved_at <= NOW()) AS approved,
                avi.approved_at
            FROM agents a
            INNER JOIN evaluations_hydrated e
                ON e.agent_id = a.agent_id
               AND e.set_id = a.set_id
               AND e.status = 'success'
               AND e.score IS NOT NULL
               AND e.score > 0
               AND e.evaluation_set_group = 'validator'::evaluationsetgroup
            LEFT JOIN approved_agents avi
                ON avi.agent_id = a.agent_id
               AND avi.set_id = a.set_id
            WHERE a.agent_id = target_agent_id
              AND a.set_id = target_set_id
        )
        SELECT
            ae.agent_id,
            ae.miner_hotkey,
            ae.name,
            ae.version_num,
            ae.created_at,
            ae.status,
            ae.set_id,
            ae.approved,
            ae.approved_at,
            COUNT(DISTINCT ae.validator_hotkey)::int,
            AVG(ae.score)
        FROM agent_evaluations ae
        GROUP BY
            ae.agent_id,
            ae.miner_hotkey,
            ae.name,
            ae.version_num,
            ae.created_at,
            ae.status,
            ae.set_id,
            ae.approved,
            ae.approved_at
        HAVING COUNT(DISTINCT ae.validator_hotkey) >= 2;
    ELSIF target_scoring_mode = 'consensus' THEN
        INSERT INTO agent_scores (
            agent_id, miner_hotkey, name, version_num, created_at, status,
            set_id, approved, approved_at, validator_count, final_score
        )
        WITH validator_evaluations AS (
            SELECT
                a.agent_id,
                a.miner_hotkey,
                a.name,
                a.version_num,
                a.created_at,
                a.status,
                e.evaluation_id,
                e.set_id,
                e.validator_hotkey,
                (avi.agent_id IS NOT NULL AND avi.approved_at <= NOW()) AS approved,
                avi.approved_at
            FROM agents a
            INNER JOIN evaluations_hydrated e
                ON e.agent_id = a.agent_id
               AND e.set_id = a.set_id
               AND e.status = 'success'
               AND e.evaluation_set_group = 'validator'::evaluationsetgroup
            LEFT JOIN approved_agents avi
                ON avi.agent_id = a.agent_id
               AND avi.set_id = a.set_id
            WHERE a.agent_id = target_agent_id
              AND a.set_id = target_set_id
        ),
        validator_counts AS (
            SELECT
                agent_id,
                miner_hotkey,
                name,
                version_num,
                created_at,
                status,
                set_id,
                BOOL_OR(approved) AS approved,
                MAX(approved_at) AS approved_at,
                COUNT(DISTINCT validator_hotkey)::int AS validator_count
            FROM validator_evaluations
            GROUP BY agent_id, miner_hotkey, name, version_num, created_at, status, set_id
        ),
        set_problem_counts AS (
            SELECT set_id, COUNT(*) AS problem_count
            FROM evaluation_sets
            WHERE set_group = 'validator'::evaluationsetgroup
              AND set_id = target_set_id
            GROUP BY set_id
        ),
        consensus_by_problem AS (
            SELECT
                ve.agent_id,
                ve.set_id,
                es.problem_name,
                COUNT(DISTINCT ve.validator_hotkey) FILTER (WHERE erh.solved IS TRUE)
                    AS solved_validator_count
            FROM validator_evaluations ve
            INNER JOIN evaluation_runs_hydrated erh
                ON erh.evaluation_id = ve.evaluation_id
            INNER JOIN evaluation_sets es
                ON es.set_id = ve.set_id
               AND es.set_group = 'validator'::evaluationsetgroup
               AND es.problem_name = erh.problem_name
            GROUP BY ve.agent_id, ve.set_id, es.problem_name
        )
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
            vc.validator_count,
            COUNT(*) FILTER (WHERE cbp.solved_validator_count = vc.validator_count)::float
                / spc.problem_count
        FROM validator_counts vc
        INNER JOIN set_problem_counts spc ON spc.set_id = vc.set_id
        INNER JOIN consensus_by_problem cbp
            ON cbp.agent_id = vc.agent_id
           AND cbp.set_id = vc.set_id
        WHERE spc.problem_count > 0
        GROUP BY
            vc.agent_id,
            vc.miner_hotkey,
            vc.name,
            vc.version_num,
            vc.created_at,
            vc.status,
            vc.set_id,
            vc.approved,
            vc.approved_at,
            vc.validator_count,
            spc.problem_count
        HAVING
            vc.validator_count >= 2
            AND COUNT(*) FILTER (WHERE cbp.solved_validator_count = vc.validator_count) > 0;
    END IF;
END;
$$ LANGUAGE plpgsql;
"""


POPULATE_AGENT_SCORES = """
CREATE FUNCTION populate_agent_scores()
RETURNS VOID AS $$
DECLARE
    target_agent_id UUID;
BEGIN
    FOR target_agent_id IN
        SELECT a.agent_id
        FROM agents a
        INNER JOIN competitions c ON c.set_id = a.set_id
        WHERE a.set_id IS NOT NULL
          AND c.scoring_mode IS NOT NULL
    LOOP
        PERFORM refresh_agent_scores_for_agent(target_agent_id);
    END LOOP;
END;
$$ LANGUAGE plpgsql;
"""


REFRESH_AGENT_SCORES_WRAPPER = """
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
        ELSIF TG_TABLE_NAME = 'unapproved_agent_ids' THEN
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
        ELSIF TG_TABLE_NAME = 'unapproved_agent_ids' THEN
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


def _add_not_valid_agent_membership_fk(
    table_name: str,
    constraint_name: str,
    *,
    on_delete: str | None = None,
) -> None:
    delete_clause = f" ON DELETE {on_delete}" if on_delete is not None else ""
    op.execute(
        f"""
        ALTER TABLE {table_name}
        ADD CONSTRAINT {constraint_name}
        FOREIGN KEY (agent_id, set_id)
        REFERENCES agents (agent_id, set_id){delete_clause}
        NOT VALID
        """
    )


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION create_competition_for_new_set_id()
        RETURNS TRIGGER AS $$
        BEGIN
            INSERT INTO competitions (set_id)
            VALUES (NEW.set_id)
            ON CONFLICT (set_id) DO NOTHING;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.add_column("pre_screening_jobs", sa.Column("set_id", sa.Integer(), nullable=True))
    op.execute("""
        UPDATE pre_screening_jobs job
        SET set_id = agent.set_id
        FROM agents agent
        WHERE agent.agent_id = job.agent_id
          AND agent.set_id IS NOT NULL
    """)

    op.create_unique_constraint("uq_agents_agent_id_set_id", "agents", ["agent_id", "set_id"])
    op.create_unique_constraint(
        "uq_approval_jobs_job_id_agent_id_set_id",
        "approval_jobs",
        ["job_id", "agent_id", "set_id"],
    )

    op.execute("""
        ALTER TABLE evaluation_sets
        ADD CONSTRAINT fk_evaluation_sets_competition
        FOREIGN KEY (set_id)
        REFERENCES competitions (set_id)
        DEFERRABLE INITIALLY DEFERRED
        NOT VALID
    """)

    op.execute("""
        CREATE FUNCTION enforce_agent_competition_membership()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.set_id IS NULL THEN
                    RAISE EXCEPTION 'new agent % must have competition membership', NEW.agent_id
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;

            IF OLD.set_id IS NULL AND NEW.set_id IS NOT NULL THEN
                RETURN NEW;
            END IF;

            IF OLD.set_id IS DISTINCT FROM NEW.set_id THEN
                RAISE EXCEPTION 'agent % competition membership is immutable', NEW.agent_id
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_agents_competition_membership
        BEFORE INSERT OR UPDATE OF set_id ON agents
        FOR EACH ROW
        EXECUTE FUNCTION enforce_agent_competition_membership()
    """)
    op.execute("""
        CREATE FUNCTION enforce_pre_screening_job_competition_membership()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.set_id IS NULL THEN
                RAISE EXCEPTION 'new pre-screening job for agent % must have competition membership', NEW.agent_id
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_pre_screening_jobs_competition_membership
        BEFORE INSERT OR UPDATE OF agent_id, set_id ON pre_screening_jobs
        FOR EACH ROW
        EXECUTE FUNCTION enforce_pre_screening_job_competition_membership()
    """)

    _add_not_valid_agent_membership_fk("evaluations", "fk_evaluations_agent_competition")
    _add_not_valid_agent_membership_fk(
        "pre_screening_jobs",
        "fk_pre_screening_jobs_agent_competition",
        on_delete="CASCADE",
    )
    _add_not_valid_agent_membership_fk(
        "approval_jobs",
        "fk_approval_jobs_agent_competition",
        on_delete="CASCADE",
    )
    _add_not_valid_agent_membership_fk(
        "agent_approval_states",
        "fk_agent_approval_states_agent_competition",
        on_delete="CASCADE",
    )
    _add_not_valid_agent_membership_fk("approved_agents", "fk_approved_agents_agent_competition")
    _add_not_valid_agent_membership_fk("agent_scores", "fk_agent_scores_agent_competition")
    op.execute("""
        ALTER TABLE approved_agents
        ADD CONSTRAINT fk_approved_agents_baseline_competition
        FOREIGN KEY (baseline_agent_id, set_id)
        REFERENCES agents (agent_id, set_id)
        NOT VALID
    """)
    op.execute("""
        ALTER TABLE agent_approval_states
        ADD CONSTRAINT fk_agent_approval_states_latest_job
        FOREIGN KEY (latest_job_id, agent_id, set_id)
        REFERENCES approval_jobs (job_id, agent_id, set_id)
        NOT VALID
    """)

    op.execute(f"ALTER FUNCTION refresh_agent_scores_for_agent(UUID) RENAME TO {_OLD_REFRESH_FUNCTION}")
    op.execute(f"ALTER FUNCTION populate_agent_scores() RENAME TO {_OLD_POPULATE_FUNCTION}")
    op.execute(REFRESH_AGENT_SCORES_FOR_AGENT)
    op.execute(POPULATE_AGENT_SCORES)
    op.execute(REFRESH_AGENT_SCORES_WRAPPER)


def downgrade() -> None:
    op.execute("DROP FUNCTION populate_agent_scores()")
    op.execute("DROP FUNCTION refresh_agent_scores_for_agent(UUID)")
    op.execute(f"ALTER FUNCTION {_OLD_REFRESH_FUNCTION}(UUID) RENAME TO refresh_agent_scores_for_agent")
    op.execute(f"ALTER FUNCTION {_OLD_POPULATE_FUNCTION}() RENAME TO populate_agent_scores")
    op.execute(REFRESH_AGENT_SCORES_WRAPPER)

    op.drop_constraint(
        "fk_agent_approval_states_latest_job",
        "agent_approval_states",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_approved_agents_baseline_competition",
        "approved_agents",
        type_="foreignkey",
    )

    for table_name, constraint_name in (
        ("agent_scores", "fk_agent_scores_agent_competition"),
        ("approved_agents", "fk_approved_agents_agent_competition"),
        ("agent_approval_states", "fk_agent_approval_states_agent_competition"),
        ("approval_jobs", "fk_approval_jobs_agent_competition"),
        ("pre_screening_jobs", "fk_pre_screening_jobs_agent_competition"),
        ("evaluations", "fk_evaluations_agent_competition"),
    ):
        op.drop_constraint(constraint_name, table_name, type_="foreignkey")

    op.execute("DROP TRIGGER trg_pre_screening_jobs_competition_membership ON pre_screening_jobs")
    op.execute("DROP FUNCTION enforce_pre_screening_job_competition_membership()")
    op.execute("DROP TRIGGER trg_agents_competition_membership ON agents")
    op.execute("DROP FUNCTION enforce_agent_competition_membership()")
    op.drop_constraint("fk_evaluation_sets_competition", "evaluation_sets", type_="foreignkey")
    op.drop_constraint(
        "uq_approval_jobs_job_id_agent_id_set_id",
        "approval_jobs",
        type_="unique",
    )
    op.drop_constraint("uq_agents_agent_id_set_id", "agents", type_="unique")
    op.drop_column("pre_screening_jobs", "set_id")

    op.execute("""
        CREATE OR REPLACE FUNCTION create_competition_for_new_set_id()
        RETURNS TRIGGER AS $$
        BEGIN
            INSERT INTO competitions (set_id, start_date)
            VALUES (NEW.set_id, NOW())
            ON CONFLICT (set_id) DO NOTHING;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
