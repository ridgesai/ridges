from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional
from uuid import UUID, uuid5

import api.config as config
import utils.logger as logger
from models.agent import (
    Agent,
    AgentCreate,
    AgentScored,
    AgentStatus,
    BenchmarkAgentScored,
    PossiblyBenchmarkAgent,
)
from models.evaluation import EvaluationStatus
from models.evaluation_set import EvaluationSetGroup
from models.queue import QueueStage
from queries.errors import DuplicateAgentIDError
from utils.agent_secrets import decrypt_agent_secret
from utils.database import DatabaseConnection, db_operation
from utils.s3 import upload_text_file_to_s3

DEFAULT_PRE_SCREENING_POLICY_VERSION = "hardcoding-v1"


@dataclass(slots=True, frozen=True)
class AgentOpenRouterSecrets:
    runtime_api_key: str
    management_api_key: str
    workspace_id: str
    api_key_label: str
    api_key_creator_user_id: str
    validated_at: datetime


def _derive_agent_id(payment_block_hash: str, payment_extrinsic_index: str) -> UUID:
    return uuid5(
        config.AGENT_UUID_NAMESPACE,
        f"{payment_block_hash}:{payment_extrinsic_index}",
    )


@db_operation
async def get_agent_by_id(conn: DatabaseConnection, agent_id: UUID) -> Optional[Agent]:
    result = await conn.fetchrow(
        """
        SELECT *
        FROM agents 
        WHERE agent_id = $1
        LIMIT 1
        """,
        agent_id,
    )

    if result is None:
        return None

    return Agent(**result)


@db_operation
async def get_possibly_benchmark_agent_by_id(
    conn: DatabaseConnection, agent_id: UUID
) -> Optional[PossiblyBenchmarkAgent]:
    result = await conn.fetchrow(
        """
        SELECT
            a.*,
            (bai.agent_id IS NOT NULL) AS is_benchmark_agent,
            bai.description AS benchmark_description
        FROM agents a
        LEFT JOIN benchmark_agent_ids bai ON a.agent_id = bai.agent_id
        WHERE a.agent_id = $1
        LIMIT 1
        """,
        agent_id,
    )

    if result is None:
        return None

    return PossiblyBenchmarkAgent(**result)


@db_operation
async def get_agent_by_evaluation_run_id(conn: DatabaseConnection, evaluation_run_id: UUID) -> Optional[Agent]:
    result = await conn.fetchrow(
        """
        SELECT * FROM agents
        WHERE agent_id = (
            SELECT agent_id FROM evaluations WHERE evaluation_id = (
                SELECT evaluation_id FROM evaluation_runs WHERE evaluation_run_id = $1 LIMIT 1
            ) LIMIT 1
        )
        """,
        evaluation_run_id,
    )

    if result is None:
        return None

    return Agent(**result)


@db_operation
async def get_all_agents_by_miner_hotkey(conn: DatabaseConnection, miner_hotkey: str) -> List[Agent]:
    result = await conn.fetch(
        """
        SELECT * FROM agents 
        WHERE miner_hotkey = $1
        ORDER BY created_at DESC
        """,
        miner_hotkey,
    )

    return [Agent(**agent) for agent in result]


@db_operation
async def get_latest_agent_for_miner_hotkey(conn: DatabaseConnection, miner_hotkey: str) -> Optional[Agent]:
    result = await conn.fetchrow(
        """
        SELECT * FROM agents 
        WHERE miner_hotkey = $1
        ORDER BY created_at DESC
        LIMIT 1
        """,
        miner_hotkey,
    )

    if result is None:
        return None

    return Agent(**result)


@db_operation
async def get_latest_agent_created_at_for_miner_hotkey_in_latest_set_id(
    conn: DatabaseConnection, miner_hotkey: str
) -> Optional[datetime]:
    result = await conn.fetchval(
        """
        SELECT MAX(a.created_at)
        FROM agents a
        WHERE a.miner_hotkey = $1
        AND a.created_at > (SELECT MAX(created_at) FROM evaluation_sets)
        """,
        miner_hotkey,
    )

    return result


@db_operation
async def create_agent(
    conn: DatabaseConnection,
    agent: AgentCreate,
    agent_text: str,
    *,
    runtime_openrouter_api_key_ciphertext: bytes,
    management_openrouter_api_key_ciphertext: bytes,
    openrouter_workspace_id: str,
    openrouter_api_key_label: str,
    openrouter_api_key_creator_user_id: str,
    openrouter_validated_at: datetime,
    create_pre_screening_job: bool = False,
    pre_screening_policy_version: str = DEFAULT_PRE_SCREENING_POLICY_VERSION,
) -> "UUID":
    """Create a new Agent record in the database and upload the agent code to S3. The agent_id is derived from the payment block hash and extrinsic index to ensure uniqueness and traceability. This function returns the generated agent_id (UUID) for the newly created agent.

    Parameters
    ----------
    conn : DatabaseConnection
        The database connection to use for the operation.
    agent : AgentCreate
        The agent schema containing the metadata for the agent to be created.
    agent_text : str
        The source code of the agent to be uploaded to S3.
    Returns
    -------
    UUID
        The UUID of the newly created agent.
    """
    # 1. Generate a new agent_id (UUID) for the agent
    agent_id = _derive_agent_id(agent.payment_block_hash, agent.payment_extrinsic_index)

    # 2. Store agent code in S3 with key as agent_id/agent.py
    await upload_text_file_to_s3(f"{agent_id}/agent.py", agent_text)

    async with conn.conn.transaction():
        # 3. Insert agent metadata into the database
        result = await conn.fetchval(
            """
            INSERT INTO agents (agent_id, miner_hotkey, name, version_num, created_at, status, ip_address)
            VALUES ($1, $2, $3, $4, NOW(), $5, $6)
            ON CONFLICT (agent_id) DO NOTHING
            RETURNING agent_id
            """,
            agent_id,
            agent.miner_hotkey,
            agent.name,
            agent.version_num,
            agent.status.value,
            agent.ip_address,
        )

        if result is None:
            raise DuplicateAgentIDError(agent_id)

        # 4. Insert OpenRouter secrets into the database
        await conn.execute(
            """
            INSERT INTO agent_openrouter_secrets (
                agent_id,
                runtime_api_key_ciphertext,
                management_api_key_ciphertext,
                workspace_id,
                api_key_label,
                api_key_creator_user_id,
                validated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            agent_id,
            runtime_openrouter_api_key_ciphertext,
            management_openrouter_api_key_ciphertext,
            openrouter_workspace_id,
            openrouter_api_key_label,
            openrouter_api_key_creator_user_id,
            openrouter_validated_at,
        )

        # 5. Optionally create a pre-screening job for the agent
        if create_pre_screening_job:
            await conn.execute(
                """
                INSERT INTO pre_screening_jobs (agent_id, policy_version)
                VALUES ($1, $2)
                """,
                agent_id,
                pre_screening_policy_version,
            )
    return agent_id


@db_operation
async def get_openrouter_secrets_for_agent_id(
    conn: DatabaseConnection, agent_id: UUID
) -> AgentOpenRouterSecrets | None:
    row = await conn.fetchrow(
        """
        SELECT
            runtime_api_key_ciphertext,
            management_api_key_ciphertext,
            workspace_id,
            api_key_label,
            api_key_creator_user_id,
            validated_at
        FROM agent_openrouter_secrets
        WHERE agent_id = $1
        LIMIT 1
        """,
        agent_id,
    )

    if row is None:
        return None

    return AgentOpenRouterSecrets(
        runtime_api_key=decrypt_agent_secret(bytes(row["runtime_api_key_ciphertext"])),
        management_api_key=decrypt_agent_secret(bytes(row["management_api_key_ciphertext"])),
        workspace_id=row["workspace_id"],
        api_key_label=row["api_key_label"],
        api_key_creator_user_id=row["api_key_creator_user_id"],
        validated_at=row["validated_at"],
    )


@db_operation
async def get_openrouter_api_key_for_agent_id(conn: DatabaseConnection, agent_id: UUID) -> str | None:
    secrets = await get_openrouter_secrets_for_agent_id(agent_id)
    return None if secrets is None else secrets.runtime_api_key


@db_operation
async def update_agent_status(conn: DatabaseConnection, agent_id: UUID, status: AgentStatus) -> None:
    await conn.execute(
        """
        UPDATE agents
        SET status = $2
        WHERE agent_id = $1
        """,
        agent_id,
        status.value,
    )


@db_operation
async def get_benchmark_agents(conn: DatabaseConnection) -> List[BenchmarkAgentScored]:
    result = await conn.fetch(
        """
        SELECT
            ass.*,
            bai.description AS benchmark_description
        FROM agent_scores ass
        LEFT JOIN benchmark_agent_ids bai ON ass.agent_id = bai.agent_id
        WHERE ass.agent_id IN (SELECT agent_id FROM benchmark_agent_ids)
        ORDER BY ass.created_at DESC, ass.final_score DESC
        """
    )

    return [BenchmarkAgentScored(**agent) for agent in result]


# TODO ADAM: fix this section


@db_operation
async def record_upload_attempt(conn: DatabaseConnection, upload_type: str, success: bool, **kwargs) -> None:
    # TODO ADAM: gross

    """Record an upload attempt in the upload_attempts table."""
    try:
        await conn.execute(
            """INSERT INTO upload_attempts (upload_type, success, hotkey, agent_name, filename,
                                            file_size_bytes, ip_address, error_type, error_message, ban_reason, http_status_code, agent_id)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)""",
            upload_type,
            success,
            kwargs.get("hotkey"),
            kwargs.get("agent_name"),
            kwargs.get("filename"),
            kwargs.get("file_size_bytes"),
            kwargs.get("ip_address"),
            kwargs.get("error_type"),
            kwargs.get("error_message"),
            kwargs.get("ban_reason"),
            kwargs.get("http_status_code"),
            kwargs.get("agent_id"),
        )
        logger.debug(
            f"Recorded upload attempt: type={upload_type}, success={success}, error_type={kwargs.get('error_type')}"
        )
    except Exception as e:
        logger.error(f"Failed to record upload attempt: {e}")


@db_operation
async def get_top_agents(conn: DatabaseConnection, number_of_agents: int = 10, page: int = 1) -> list[AgentScored]:
    """Retrieve the top agents.

    Agents are ordered by validator score, then average validator-evaluation cost, then creation time.

    You can specify the number of results to return and the page number (for pagination).

    Parameters
    ----------
    conn : DatabaseConnection
        Database connection to use for the query
    number_of_agents : int, optional
        Number of agents to return, by default 10
    page : int, optional
        Page number for pagination, by default 1

    Returns
    -------
    list[AgentScored]
        List of top agents with their scores.
    """
    # TODO ADAM: this query was supposed to be fixed to remove the pagination concept
    # TODO ADAM: maybe edge case bugs here if pagenum is 0,negative,or too high etc
    offset = (page - 1) * number_of_agents

    results = await conn.fetch(
        """
        select ass.*
        from agent_scores ass
        left join lateral (
            select avg(eh.avg_cost_usd) as avg_cost_usd
            from evaluations_hydrated eh
            where eh.agent_id             = ass.agent_id
              and eh.set_id               = ass.set_id
              and eh.evaluation_set_group = 'validator'::EvaluationSetGroup
              and eh.status               = 'success'::EvaluationStatus
        ) rt on true
        where ass.set_id = (select max(set_id) from evaluation_sets)
        and ass.agent_id not in (select agent_id from benchmark_agent_ids)
        and ass.status::text <> 'cancelled'
        order by
            round(ass.final_score::numeric, 6) desc,
            rt.avg_cost_usd asc nulls last,
            ass.created_at asc
        limit $1 offset $2
        """,
        number_of_agents,
        offset,
    )

    return [AgentScored(**agent) for agent in results]


@db_operation
async def get_agents_in_queue(conn: DatabaseConnection, queue_stage: QueueStage) -> list[Agent]:
    # TODO ALEX from ADAM: Modify this in the view itself rather than branching explicitly here.
    # The view apparently does not sort by created_at.
    queue_to_query = f"{queue_stage.value}_queue"

    if queue_stage in (QueueStage.pre_screening, QueueStage.screener_1):
        queue = await conn.fetch(f"""
            SELECT a.*
            from agents a
            join {queue_to_query} q on q.agent_id = a.agent_id
            order by a.created_at asc
        """)

        return [Agent(**agent) for agent in queue]

    queue = await conn.fetch(f"""
        SELECT a.*
        from agents a
        join {queue_to_query} q on q.agent_id = a.agent_id
    """)

    return [Agent(**agent) for agent in queue]


@db_operation
async def get_next_agent_id_awaiting_evaluation_for_validator_hotkey(
    conn: DatabaseConnection, validator_hotkey: str
) -> Optional[UUID]:
    if validator_hotkey.startswith("screener-1"):
        result = await conn.fetchrow("""
            SELECT agent_id FROM screener_1_queue LIMIT 1
        """)
    elif validator_hotkey.startswith("screener-2"):
        result = await conn.fetchrow("""
             SELECT agent_id FROM screener_2_queue LIMIT 1
        """)
    else:
        # The query is structured to force a candidates-first execution order, avoiding a
        # full scan of evaluation_runs that the planner would otherwise choose.
        #
        # Root cause of the bad plan: evaluations_hydrated is a view that groups by
        # evaluation_id. PostgreSQL cannot push a predicate past a GROUP BY from outside
        # the view, so any WHERE agent_id IN (...) filter applied to the view is evaluated
        # *after* the full aggregation — meaning all evaluation_runs are scanned and the
        # JSONB solved computation runs on every row before the candidate filter is applied.
        #
        # Here we bypass the view entirely and inline its logic using an explicit join chain:
        #   candidates (MATERIALIZED, ~1–50 rows)
        #     → evaluations by agent_id (index seek, ~10–50 rows per candidate)
        #       → evaluation_runs via JOIN LATERAL by evaluation_id (index seek, ~20–50 rows each)
        result = await conn.fetchrow(
            f"""
            WITH candidates AS MATERIALIZED (
                SELECT
                    agent_id,
                    created_at
                FROM
                    agents
                WHERE
                    agents.status = '{AgentStatus.evaluating.value}'
                    AND NOT EXISTS (
                        SELECT
                            1
                        FROM
                            benchmark_agent_ids b
                        WHERE
                            b.agent_id = agents.agent_id
                    )
            ),
            combined_eval_stats AS (
                SELECT
                    c.agent_id,
                    BOOL_OR(
                        e.validator_hotkey = $1
                        AND e.evaluation_set_group = '{EvaluationSetGroup.validator.value}' :: EvaluationSetGroup
                    ) AS already_evaluated,
                    COUNT(*) FILTER (
                        WHERE
                            e.evaluation_set_group = '{EvaluationSetGroup.validator.value}' :: EvaluationSetGroup
                            AND agg.computed_status = '{EvaluationStatus.running.value}' :: EvaluationStatus
                    ) AS num_running_evals,
                    COUNT(*) FILTER (
                        WHERE
                            e.evaluation_set_group = '{EvaluationSetGroup.validator.value}' :: EvaluationSetGroup
                            AND agg.computed_status = '{EvaluationStatus.success.value}' :: EvaluationStatus
                    ) AS num_finished_evals,
                    COALESCE(
                        MAX(agg.score) FILTER (
                            WHERE
                                e.evaluation_set_group = '{EvaluationSetGroup.screener_2.value}' :: EvaluationSetGroup
                                AND agg.computed_status = '{EvaluationStatus.success.value}' :: EvaluationStatus
                        ),
                        0
                    ) AS screener_2_score
                FROM
                    candidates c
                    JOIN evaluations e ON e.agent_id = c.agent_id
                    AND e.evaluation_set_group IN (
                        '{EvaluationSetGroup.validator.value}' :: EvaluationSetGroup,
                        '{EvaluationSetGroup.screener_2.value}' :: EvaluationSetGroup
                    )
                    JOIN LATERAL (
                        SELECT
                            (
                                CASE
                                    WHEN EVERY(
                                        er.status = 'finished'
                                        OR (
                                            er.status = 'error'
                                            AND er.error_code BETWEEN 1000
                                            AND 1999
                                        )
                                    ) THEN 'success' :: EvaluationStatus
                                    WHEN EVERY(er.status IN ('finished', 'error')) THEN 'failure' :: EvaluationStatus
                                    ELSE 'running' :: EvaluationStatus
                                END
                            ) AS computed_status,
                            COUNT(*) FILTER (WHERE er.solved) :: float / NULLIF(COUNT(*), 0) AS score
                        FROM
                            evaluation_runs_hydrated er
                        WHERE
                            er.evaluation_id = e.evaluation_id
                        HAVING COUNT(*) > 0
                    ) agg ON (
                        (
                            e.evaluation_set_group = '{EvaluationSetGroup.validator.value}' :: EvaluationSetGroup
                            AND agg.computed_status IN (
                                '{EvaluationStatus.success.value}' :: EvaluationStatus,
                                '{EvaluationStatus.running.value}' :: EvaluationStatus
                            )
                        )
                        OR (
                            e.evaluation_set_group = '{EvaluationSetGroup.screener_2.value}' :: EvaluationSetGroup
                            AND agg.computed_status = '{EvaluationStatus.success.value}' :: EvaluationStatus
                        )
                    )
                GROUP BY
                    c.agent_id
            )
            SELECT
                c.agent_id,
                COALESCE(s.num_running_evals, 0) AS num_running_evals,
                COALESCE(s.num_finished_evals, 0) AS num_finished_evals
            FROM
                candidates c
                LEFT JOIN combined_eval_stats s ON s.agent_id = c.agent_id
            WHERE
                NOT COALESCE(s.already_evaluated, false)
                AND COALESCE(s.num_running_evals, 0) + COALESCE(s.num_finished_evals, 0) < $2
            ORDER BY
                COALESCE(s.screener_2_score, 0) DESC,
                c.created_at ASC
            LIMIT
                1
            """,
            validator_hotkey,
            config.NUM_EVALS_PER_AGENT,
        )

    if result is None:
        return None

    return result["agent_id"]
