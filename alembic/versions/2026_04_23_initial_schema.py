"""initial schema

Revision ID: 159d505b4ec8
Revises:
Create Date: 2026-04-23 12:16:04.824300

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "159d505b4ec8"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'agentstatus') THEN
                CREATE TYPE agentstatus AS ENUM (
                    'screening_1', 'failed_screening_1', 'screening_2',
                    'failed_screening_2', 'evaluating', 'finished'
                );
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'evaluationsetgroup') THEN
                CREATE TYPE evaluationsetgroup AS ENUM ('screener_1', 'screener_2', 'validator');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'evaluationrunstatus') THEN
                CREATE TYPE evaluationrunstatus AS ENUM (
                    'pending', 'initializing_agent', 'running_agent',
                    'initializing_eval', 'running_eval', 'finished', 'error'
                );
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'evaluationrunlogtype') THEN
                CREATE TYPE evaluationrunlogtype AS ENUM ('agent', 'eval');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'evaluationstatus') THEN
                CREATE TYPE evaluationstatus AS ENUM ('running', 'success', 'failure');
            END IF;
        END $$;
    """
    )

    op.create_table(
        "agents",
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("miner_hotkey", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("version_num", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="agentstatus", create_type=False),
            nullable=True,
        ),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("ip_address", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("agent_id"),
    )
    op.create_index(
        "idx_agents_miner_hotkey_version",
        "agents",
        ["miner_hotkey", "agent_id"],
    )
    op.create_index(
        "idx_agents_status",
        "agents",
        ["status"],
        postgresql_where=sa.text("status = 'evaluating'"),
    )

    op.create_table(
        "banned_hotkeys",
        sa.Column("miner_hotkey", sa.Text(), nullable=False),
        sa.Column("banned_reason", sa.Text(), nullable=True),
        sa.Column(
            "banned_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("miner_hotkey"),
    )
    op.create_index(
        "idx_banned_hotkeys_miner_hotkey", "banned_hotkeys", ["miner_hotkey"]
    )

    op.create_table(
        "evaluation_sets",
        sa.Column("set_id", sa.Integer(), nullable=False),
        sa.Column(
            "set_group",
            postgresql.ENUM(name="evaluationsetgroup", create_type=False),
            nullable=False,
        ),
        sa.Column("problem_name", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("set_id"),
    )

    op.create_table(
        "infinite_swe_problems",
        sa.Column("problem_name", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("problem_name"),
    )

    op.create_table(
        "upload_attempts",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("upload_type", sa.Text(), nullable=False),
        sa.Column("hotkey", sa.Text(), nullable=True),
        sa.Column("agent_name", sa.Text(), nullable=True),
        sa.Column("filename", sa.Text(), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("ip_address", sa.Text(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error_type", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("ban_reason", sa.Text(), nullable=True),
        sa.Column("http_status_code", sa.Integer(), nullable=True),
        sa.Column("agent_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "benchmark_agent_ids",
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.agent_id"]),
        sa.PrimaryKeyConstraint("agent_id"),
    )

    op.create_table(
        "unapproved_agent_ids",
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("unapproved_reason", sa.Text(), nullable=True),
        sa.Column(
            "unapproved_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.agent_id"]),
        sa.PrimaryKeyConstraint("agent_id"),
    )

    op.create_table(
        "evaluations",
        sa.Column("evaluation_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("validator_hotkey", sa.Text(), nullable=False),
        sa.Column("set_id", sa.Integer(), nullable=False),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "evaluation_set_group",
            postgresql.ENUM(name="evaluationsetgroup", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.agent_id"]),
        sa.PrimaryKeyConstraint("evaluation_id"),
    )
    op.create_index("idx_evaluations_id", "evaluations", ["evaluation_id"])
    op.create_index("idx_evaluations_agent_id", "evaluations", ["agent_id"])
    op.create_index(
        "idx_evaluations_set_group_agent_id",
        "evaluations",
        ["evaluation_set_group", "agent_id"],
    )
    op.create_index(
        "idx_evaluations_validator_pattern",
        "evaluations",
        ["validator_hotkey"],
        postgresql_ops={"validator_hotkey": "text_pattern_ops"},
    )

    op.create_table(
        "evaluation_runs",
        sa.Column("evaluation_run_id", sa.UUID(), nullable=False),
        sa.Column("evaluation_id", sa.UUID(), nullable=False),
        sa.Column("problem_name", sa.Text(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="evaluationrunstatus", create_type=False),
            nullable=True,
        ),
        sa.Column("patch", sa.Text(), nullable=True),
        sa.Column(
            "test_results",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("error_code", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "started_initializing_agent_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "started_running_agent_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "started_initializing_eval_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "started_running_eval_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "finished_or_errored_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_id"], ["evaluations.evaluation_id"]
        ),
        sa.PrimaryKeyConstraint("evaluation_run_id"),
    )
    op.create_index(
        "idx_evaluation_runs_evaluation_id",
        "evaluation_runs",
        ["evaluation_id"],
    )

    op.create_table(
        "evaluation_run_logs",
        sa.Column("evaluation_run_id", sa.UUID(), nullable=False),
        sa.Column("logs", sa.Text(), nullable=True),
        sa.Column(
            "type",
            postgresql.ENUM(name="evaluationrunlogtype", create_type=False),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("evaluation_run_id", "type"),
    )

    op.create_table(
        "inferences",
        sa.Column(
            "inference_id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("evaluation_run_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False),
        sa.Column(
            "messages", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("response", sa.Text(), nullable=True),
        sa.Column("num_input_tokens", sa.Integer(), nullable=True),
        sa.Column("num_output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column(
            "request_received_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "response_sent_at", sa.TIMESTAMP(timezone=True), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"], ["evaluation_runs.evaluation_run_id"]
        ),
        sa.PrimaryKeyConstraint("inference_id"),
    )
    op.create_index(
        "idx_inferences_created_provider_range",
        "inferences",
        ["request_received_at", "provider"],
        postgresql_include=[
            "response_sent_at",
            "status_code",
            "num_input_tokens",
            "num_output_tokens",
            "cost_usd",
        ],
        postgresql_where=sa.text(
            "response_sent_at IS NOT NULL AND provider IS NOT NULL"
        ),
    )

    op.create_table(
        "embeddings",
        sa.Column("embedding_id", sa.UUID(), nullable=False),
        sa.Column("evaluation_run_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("input", sa.Text(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column(
            "response", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("num_input_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column(
            "request_received_at", sa.TIMESTAMP(timezone=True), nullable=False
        ),
        sa.Column(
            "response_sent_at", sa.TIMESTAMP(timezone=True), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"], ["evaluation_runs.evaluation_run_id"]
        ),
        sa.PrimaryKeyConstraint("embedding_id"),
    )

    op.create_table(
        "approved_agents",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("agent_id", sa.UUID(), nullable=True),
        sa.Column("set_id", sa.Integer(), nullable=False),
        sa.Column(
            "approved_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.agent_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", "set_id"),
    )

    op.create_table(
        "evaluation_payments",
        sa.Column("payment_block_hash", sa.Text(), nullable=False),
        sa.Column("payment_extrinsic_index", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("miner_hotkey", sa.Text(), nullable=False),
        sa.Column("miner_coldkey", sa.Text(), nullable=False),
        sa.Column("amount_rao", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.agent_id"]),
        sa.PrimaryKeyConstraint(
            "payment_block_hash", "payment_extrinsic_index"
        ),
    )

    op.create_table(
        "agent_scores",
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("miner_hotkey", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("version_num", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="agentstatus", create_type=False),
            nullable=False,
        ),
        sa.Column("set_id", sa.Integer(), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=True),
        sa.Column("approved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("validator_count", sa.Integer(), nullable=False),
        sa.Column("final_score", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("agent_id"),
    )
    op.create_index(
        "idx_agent_scores_agent_id", "agent_scores", ["agent_id"], unique=True
    )
    op.create_index(
        "idx_agent_scores_final_score", "agent_scores", ["final_score"]
    )
    op.create_index(
        "idx_agent_scores_created_at", "agent_scores", ["created_at"]
    )

    op.execute(
        """
        CREATE OR REPLACE VIEW evaluation_runs_with_cost AS
        SELECT
            er.*,
            COALESCE(SUM(i.cost_usd), 0) AS total_cost_usd,
            COALESCE(SUM(i.num_input_tokens), 0) AS total_input_tokens,
            COALESCE(SUM(i.num_output_tokens), 0) AS total_output_tokens,
            COUNT(*) as num_inferences
        FROM evaluation_runs er
        LEFT JOIN inferences i ON er.evaluation_run_id = i.evaluation_run_id
        GROUP BY er.evaluation_run_id;
    """
    )

    op.execute(
        """
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
    """
    )

    op.execute(
        """
        CREATE OR REPLACE VIEW evaluations_hydrated AS
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
    """
    )

    op.execute(
        """
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
    """
    )

    op.execute(
        """
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
    """
    )

    op.execute(
        """
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
            AND agents.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
            AND agents.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
        ORDER BY
            screener_2_scores.score DESC,
            agents.created_at ASC,
            num_finished_evals DESC;
    """
    )

    op.execute(
        """
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
    )

    op.execute(
        """
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
    )

    op.execute(
        """
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
                ELSIF TG_TABLE_NAME = 'banned_hotkeys' THEN
                    DELETE FROM agent_scores
                    WHERE agent_id IN (SELECT agent_id FROM agents WHERE miner_hotkey = OLD.miner_hotkey);
                    RETURN OLD;
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
                ELSIF TG_TABLE_NAME = 'banned_hotkeys' THEN
                    DELETE FROM agent_scores
                    WHERE agent_id IN (SELECT agent_id FROM agents WHERE miner_hotkey = NEW.miner_hotkey);
                    RETURN NEW;
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
    )

    op.execute(
        "DROP TRIGGER IF EXISTS tr_refresh_agent_scores ON evaluations;"
    )
    op.execute(
        """
        CREATE TRIGGER tr_refresh_agent_scores
        AFTER INSERT OR UPDATE OR DELETE ON evaluations
        FOR EACH ROW EXECUTE PROCEDURE refresh_agent_scores();
    """
    )
    op.execute(
        "DROP TRIGGER IF EXISTS tr_refresh_agent_scores_approved_agents ON approved_agents;"
    )
    op.execute(
        """
        CREATE TRIGGER tr_refresh_agent_scores_approved_agents
        AFTER INSERT OR UPDATE OR DELETE ON approved_agents
        FOR EACH ROW EXECUTE PROCEDURE refresh_agent_scores();
    """
    )
    op.execute(
        "DROP TRIGGER IF EXISTS tr_refresh_agent_scores_banned_hotkeys ON banned_hotkeys;"
    )
    op.execute(
        """
        CREATE TRIGGER tr_refresh_agent_scores_banned_hotkeys
        AFTER INSERT OR UPDATE OR DELETE ON banned_hotkeys
        FOR EACH ROW EXECUTE PROCEDURE refresh_agent_scores();
    """
    )
    op.execute(
        "DROP TRIGGER IF EXISTS tr_refresh_agent_scores_delete_agents ON agents;"
    )
    op.execute(
        """
        CREATE TRIGGER tr_refresh_agent_scores_delete_agents
        AFTER INSERT OR UPDATE OR DELETE ON agents
        FOR EACH ROW EXECUTE PROCEDURE refresh_agent_scores();
    """
    )
    op.execute(
        "DROP TRIGGER IF EXISTS tr_refresh_agent_scores_unapproved_agent_ids ON unapproved_agent_ids;"
    )
    op.execute(
        """
        CREATE TRIGGER tr_refresh_agent_scores_unapproved_agent_ids
        AFTER INSERT OR UPDATE OR DELETE ON unapproved_agent_ids
        FOR EACH ROW EXECUTE PROCEDURE refresh_agent_scores();
    """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS tr_refresh_agent_scores_unapproved_agent_ids ON unapproved_agent_ids;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS tr_refresh_agent_scores_delete_agents ON agents;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS tr_refresh_agent_scores_banned_hotkeys ON banned_hotkeys;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS tr_refresh_agent_scores_approved_agents ON approved_agents;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS tr_refresh_agent_scores ON evaluations;"
    )
    op.execute("DROP FUNCTION IF EXISTS refresh_agent_scores() CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS populate_agent_scores() CASCADE;")
    op.execute(
        "DROP FUNCTION IF EXISTS refresh_agent_scores_for_agent(UUID) CASCADE;"
    )
    op.execute("DROP VIEW IF EXISTS validator_queue;")
    op.execute("DROP VIEW IF EXISTS screener_2_queue;")
    op.execute("DROP VIEW IF EXISTS screener_1_queue;")
    op.execute("DROP VIEW IF EXISTS evaluations_hydrated;")
    op.execute("DROP VIEW IF EXISTS evaluation_runs_hydrated;")
    op.execute("DROP VIEW IF EXISTS evaluation_runs_with_cost;")

    op.drop_index("idx_agent_scores_created_at", table_name="agent_scores")
    op.drop_index("idx_agent_scores_final_score", table_name="agent_scores")
    op.drop_index("idx_agent_scores_agent_id", table_name="agent_scores")
    op.drop_table("agent_scores")
    op.drop_table("evaluation_payments")
    op.drop_table("approved_agents")
    op.drop_table("embeddings")
    op.drop_index(
        "idx_inferences_created_provider_range",
        table_name="inferences",
        postgresql_include=[
            "response_sent_at",
            "status_code",
            "num_input_tokens",
            "num_output_tokens",
            "cost_usd",
        ],
        postgresql_where=sa.text(
            "response_sent_at IS NOT NULL AND provider IS NOT NULL"
        ),
    )
    op.drop_table("inferences")
    op.drop_table("evaluation_run_logs")
    op.drop_index(
        "idx_evaluation_runs_evaluation_id", table_name="evaluation_runs"
    )
    op.drop_table("evaluation_runs")
    op.drop_index(
        "idx_evaluations_validator_pattern",
        table_name="evaluations",
        postgresql_ops={"validator_hotkey": "text_pattern_ops"},
    )
    op.drop_index(
        "idx_evaluations_set_group_agent_id", table_name="evaluations"
    )
    op.drop_index("idx_evaluations_agent_id", table_name="evaluations")
    op.drop_index("idx_evaluations_id", table_name="evaluations")
    op.drop_table("evaluations")
    op.drop_table("unapproved_agent_ids")
    op.drop_table("benchmark_agent_ids")
    op.drop_table("upload_attempts")
    op.drop_table("infinite_swe_problems")
    op.drop_table("evaluation_sets")
    op.drop_index(
        "idx_banned_hotkeys_miner_hotkey", table_name="banned_hotkeys"
    )
    op.drop_table("banned_hotkeys")
    op.drop_index(
        "idx_agents_status",
        table_name="agents",
        postgresql_where=sa.text("status = 'evaluating'"),
    )
    op.drop_index("idx_agents_miner_hotkey_version", table_name="agents")
    op.drop_table("agents")

    op.execute("DROP TYPE IF EXISTS evaluationstatus;")
    op.execute("DROP TYPE IF EXISTS evaluationrunlogtype;")
    op.execute("DROP TYPE IF EXISTS evaluationrunstatus;")
    op.execute("DROP TYPE IF EXISTS evaluationsetgroup;")
    op.execute("DROP TYPE IF EXISTS agentstatus;")
