import asyncpg
from uuid import UUID
from typing import Optional, Final
import api.config as config
import utils.logger as logger
from models.agent import Agent
from models.agent import Agent, AgentScored
from utils.database import db_operation
from models.evaluation import EvaluationStatus
from models.evaluation_set import EvaluationSetGroup
from models.agent import Agent, AgentScored, AgentStatus
from utils.s3 import upload_text_file_to_s3


@db_operation
async def get_next_agent_id_awaiting_evaluation_for_validator_hotkey(conn: asyncpg.Connection, validator_hotkey: str) -> Optional[UUID]:
    if validator_hotkey.startswith("screener-1"):
        result = await conn.fetchrow("""
            SELECT agent_id FROM screener_1_queue LIMIT 1
        """)
    elif validator_hotkey.startswith("screener-2"):
        result = await conn.fetchrow("""
             SELECT agent_id FROM screener_2_queue LIMIT 1
        """)
    else:
        result = await conn.fetchrow(
            f"""
            WITH
                validator_eval_counts AS (
                    SELECT
                        agent_id,
                        BOOL_OR(validator_hotkey = $1) AS already_evaluated,
                        COUNT(*) FILTER (WHERE status = '{EvaluationStatus.running.value}') AS num_running_evals,
                        COUNT(*) FILTER (WHERE status = '{EvaluationStatus.success.value}') AS num_finished_evals
                    FROM evaluations_hydrated
                    WHERE evaluations_hydrated.status IN ('{EvaluationStatus.success.value}', '{EvaluationStatus.running.value}')
                      AND validator_hotkey NOT LIKE 'screener-%'
                    GROUP BY agent_id
                ),
                screener_2_scores AS (
                    SELECT agent_id, MAX(score) AS score FROM evaluations_hydrated
                    WHERE validator_hotkey LIKE 'screener-2%'
                      AND evaluations_hydrated.status = '{EvaluationStatus.success.value}'
                    GROUP BY agent_id
                )
            SELECT
                agent_id,
                num_running_evals,
                num_finished_evals
            FROM agents
                 INNER JOIN screener_2_scores USING (agent_id)
                 LEFT JOIN validator_eval_counts USING (agent_id)
            WHERE
                agents.status = '{AgentStatus.evaluating.value}'
              AND NOT COALESCE(already_evaluated, false)
              AND num_running_evals + num_finished_evals < $2
            ORDER BY
                screener_2_scores.score DESC,
                agents.created_at ASC
            LIMIT 1
            """,
            validator_hotkey,
            config.NUM_EVALS_PER_AGENT
        )

    if result is None or "agent_id" not in result:
        return None

    return result["agent_id"]


@db_operation
async def get_agent_by_id(conn: asyncpg.Connection, agent_id: UUID) -> Optional[Agent]:
    result = await conn.fetchrow("""
        SELECT
            *
        FROM agents 
        WHERE agent_id = $1
        LIMIT 1
    """, agent_id)

    if result is None:
        return None

    return Agent(**result)

@db_operation
async def get_latest_agent_for_hotkey(conn: asyncpg.Connection, miner_hotkey: str) -> Optional[Agent]:
    result = await conn.fetchrow("""
        select * from agents 
        where miner_hotkey = $1
        order by created_at desc
        limit 1
    """, miner_hotkey)

    if result is None:
        return None 
    
    return Agent(**result)

@db_operation
async def create_agent(conn: asyncpg.Connection, agent: Agent, agent_text: str) -> None:
    await upload_text_file_to_s3(f"{agent.agent_id}/agent.py", agent_text)

    await conn.execute(
        f"""
                INSERT INTO agents (agent_id, miner_hotkey, name, version_num, created_at, status, ip_address)
                VALUES ($1, $2, $3, $4, NOW(), '{AgentStatus.screening_1.value}', $5)
                """,
        agent.agent_id,
        agent.miner_hotkey,
        agent.name,
        agent.version_num,
        agent.ip_address,
    )

@db_operation
async def get_agents_in_queue(conn: asyncpg.Connection, queue_stage: EvaluationSetGroup) -> list[Agent]:
    queue_to_query = f"{queue_stage.value}_queue"

    queue = await conn.fetch(f"""
        SELECT *
        from agents a
        join {queue_to_query} q on q.agent_id = a.agent_id
    """)

    return [Agent(**agent) for agent in queue]




@db_operation
async def get_top_agents(conn: asyncpg.Connection, number_of_agents: int = 10) -> list[AgentScored]:
    results = await conn.fetch(
        """
        select * from agent_scores 
        where set_id = (select max(set_id) from evaluation_sets)
        order by final_score desc
        limit $1
        """, number_of_agents
    )

    return [AgentScored(**agent) for agent in results]

@db_operation
async def record_upload_attempt(conn: asyncpg.Connection, upload_type: str, success: bool, **kwargs) -> None:
    # TODO ADAM: gross
   
   
    """Record an upload attempt in the upload_attempts table."""
    try:
        await conn.execute(
            """INSERT INTO upload_attempts (upload_type, success, hotkey, agent_name, filename,
                                            file_size_bytes, ip_address, error_type, error_message, ban_reason, http_status_code, agent_id)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)""",
            upload_type, success, kwargs.get('hotkey'), kwargs.get('agent_name'), kwargs.get('filename'),
            kwargs.get('file_size_bytes'), kwargs.get('ip_address'), kwargs.get('error_type'),
            kwargs.get('error_message'), kwargs.get('ban_reason'), kwargs.get('http_status_code'), kwargs.get('agent_id')
        )
        logger.debug(f"Recorded upload attempt: type={upload_type}, success={success}, error_type={kwargs.get('error_type')}")
    except Exception as e:
        logger.error(f"Failed to record upload attempt: {e}")



