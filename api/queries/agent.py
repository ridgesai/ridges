import asyncpg

from typing import Optional, Final
from api.src.backend.db_manager import db_operation
from uuid import UUID

MIN_EVALS: Final[int] = 3


@db_operation
async def get_next_agent_id_awaiting_evaluation_for_validator_hotkey(conn: asyncpg.Connection, validator_hotkey: str) -> Optional[UUID]:
    if validator_hotkey.startswith("screener-1"):
        result = await conn.fetchrow("""
            SELECT agent_id FROM screener_1_queue limit 1
        """)
    elif validator_hotkey.startswith("screener-2"):
        result = await conn.fetchrow("""
             SELECT agent_id FROM screener_2_queue limit 1
        """)
    else:
        result = await conn.fetchrow(
            """
            with  
                validator_eval_counts as (  
                    select  
                        agent_id,  
                        count(*) as num_evals,  
                        bool_or(validator_hotkey = $1) as already_evaluated  
                    from evaluations_hydrated  
                    where evaluations_hydrated.status = 'success'  
                      and validator_hotkey not like 'screener-%'  
                    group by agent_id  
                ),  
                screener_2_scores as (  
                    select agent_id, MAX(score) as score from evaluations_hydrated  
                    where validator_hotkey like 'screener-2%'  
                      and evaluations_hydrated.status = 'success'  
                    group by agent_id  
                )  
            select  
                agent_id
            from agents  
                 inner join screener_2_scores using (agent_id)
                 left join validator_eval_counts using (agent_id)
            where  
                agents.status = 'evaluating'
                and not COALESCE(already_evaluated, false)
                and not COALESCE(num_evals, 0) < $2
            order by  
                screener_2_scores.score desc,  
                agents.created_at asc
            """,
            validator_hotkey,
            MIN_EVALS
        )
        return UUID(result["agent_id"])