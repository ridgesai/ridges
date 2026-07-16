"""Add coldkey bans and agent coldkey provenance.

Revision ID: b7e4d2c9a106
Revises: c8f41e9a2b37
Create Date: 2026-07-10 00:00:00.000000

"""

import re
from typing import Match, Sequence, Union

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

_REFRESH_AGENT_HOTKEY_FILTER = "          AND a.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)\n"
_REFRESH_AGENT_UNAPPROVED_FILTER = "          AND a.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)\n"
_POPULATE_HOTKEY_FILTER = (
    "        WHERE miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)\n"
    "          AND agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)"
)
_POPULATE_UNAPPROVED_FILTER = "        WHERE agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)"


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


def _replace_function_fragment(signature: str, old: str, new: str) -> None:
    definition = (
        op.get_bind()
        .execute(
            sa.text("SELECT pg_get_functiondef(to_regprocedure(:signature))"),
            {"signature": signature},
        )
        .scalar_one()
    )
    if definition is None or definition.count(old) != 1:
        raise RuntimeError(f"Unexpected definition for {signature}")
    op.execute(definition.replace(old, new))


def _replace_function_pattern(signature: str, pattern: str, replacement: str) -> None:
    definition = (
        op.get_bind()
        .execute(
            sa.text("SELECT pg_get_functiondef(to_regprocedure(:signature))"),
            {"signature": signature},
        )
        .scalar_one()
    )
    if definition is None:
        raise RuntimeError(f"Missing function {signature}")
    updated, replacement_count = re.subn(pattern, replacement, definition, flags=re.MULTILINE)
    if replacement_count != 1:
        raise RuntimeError(f"Unexpected definition for {signature}")
    op.execute(updated)


def _hotkey_trigger_branch_pattern(row: str) -> str:
    return (
        rf"^(?P<indent>[ \t]*)ELSIF TG_TABLE_NAME = 'banned_hotkeys' THEN\n"
        rf"(?P=indent)    DELETE FROM agent_scores\n"
        rf"(?P=indent)    WHERE agent_id IN "
        rf"\(SELECT agent_id FROM agents WHERE miner_hotkey = {row}\.miner_hotkey\);\n"
        rf"(?P=indent)    RETURN {row};\n"
    )


def _restore_hotkey_trigger_branch(row: str) -> None:
    signature = "refresh_agent_scores()"
    definition = (
        op.get_bind()
        .execute(
            sa.text("SELECT pg_get_functiondef(to_regprocedure(:signature))"),
            {"signature": signature},
        )
        .scalar_one()
    )
    if definition is None:
        raise RuntimeError(f"Missing function {signature}")

    pattern = (
        rf"^(?P<indent>[ \t]*)ELSIF TG_TABLE_NAME = 'approved_agents' THEN\n"
        rf"(?P=indent)    affected_agent_id := {row}\.agent_id;\n"
        rf"(?P=indent)ELSIF TG_TABLE_NAME = 'unapproved_agent_ids' THEN\n"
    )

    def add_branch(match: Match[str]) -> str:
        indent = match.group("indent")
        return (
            f"{indent}ELSIF TG_TABLE_NAME = 'approved_agents' THEN\n"
            f"{indent}    affected_agent_id := {row}.agent_id;\n"
            f"{indent}ELSIF TG_TABLE_NAME = 'banned_hotkeys' THEN\n"
            f"{indent}    DELETE FROM agent_scores\n"
            f"{indent}    WHERE agent_id IN "
            f"(SELECT agent_id FROM agents WHERE miner_hotkey = {row}.miner_hotkey);\n"
            f"{indent}    RETURN {row};\n"
            f"{indent}ELSIF TG_TABLE_NAME = 'unapproved_agent_ids' THEN\n"
        )

    updated, replacement_count = re.subn(pattern, add_branch, definition, flags=re.MULTILINE)
    if replacement_count != 1:
        raise RuntimeError(f"Unexpected definition for {signature}")
    op.execute(updated)


def _remove_hotkey_score_behavior() -> None:
    _replace_function_fragment(
        "refresh_agent_scores_for_agent(uuid)",
        _REFRESH_AGENT_HOTKEY_FILTER,
        "",
    )
    _replace_function_fragment(
        "populate_agent_scores()",
        _POPULATE_HOTKEY_FILTER,
        _POPULATE_UNAPPROVED_FILTER,
    )
    _replace_function_pattern(
        "refresh_agent_scores()",
        _hotkey_trigger_branch_pattern("OLD"),
        "",
    )
    _replace_function_pattern(
        "refresh_agent_scores()",
        _hotkey_trigger_branch_pattern("NEW"),
        "",
    )


def _restore_hotkey_score_behavior() -> None:
    _replace_function_fragment(
        "refresh_agent_scores_for_agent(uuid)",
        _REFRESH_AGENT_UNAPPROVED_FILTER,
        _REFRESH_AGENT_HOTKEY_FILTER + _REFRESH_AGENT_UNAPPROVED_FILTER,
    )
    _replace_function_fragment(
        "populate_agent_scores()",
        _POPULATE_UNAPPROVED_FILTER,
        _POPULATE_HOTKEY_FILTER,
    )
    _restore_hotkey_trigger_branch("OLD")
    _restore_hotkey_trigger_branch("NEW")


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
    _remove_hotkey_score_behavior()
    op.execute("DROP TRIGGER IF EXISTS tr_refresh_agent_scores_banned_hotkeys ON banned_hotkeys;")


def downgrade() -> None:
    _restore_hotkey_score_behavior()
    op.execute("""
        CREATE TRIGGER tr_refresh_agent_scores_banned_hotkeys
        AFTER INSERT OR UPDATE OR DELETE ON banned_hotkeys
        FOR EACH ROW EXECUTE PROCEDURE refresh_agent_scores();
    """)
    _replace_queue_views(_HOTKEY_BAN_CONDITION)

    op.drop_column("agents", "miner_coldkey")
    op.drop_table("banned_coldkeys")
