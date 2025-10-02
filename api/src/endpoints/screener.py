"""
All logic around screeners, including starting a screening, finishing it, handling state updates, etc
"""

import asyncio
from typing import Optional
from api.src.backend.entities import AgentStatus
from logging import getLogger

from api.src.backend.queries.agents import set_agent_status
from api.src.backend.queries.evaluations import get_evaluation_by_evaluation_id, update_evaluation_to_error
from api.src.endpoints.agents import get_agent_by_version

logger = getLogger(__name__)

SCREENING_STATUSES = [AgentStatus.screening_1.value, AgentStatus.screening_2.value]

async def start_screening():
    pass

async def finish_screening(
    evaluation_id: str,
    screener_hotkey: str,
    errored: bool = False,
    reason: Optional[str] = None
):
    evaluation = await get_evaluation_by_evaluation_id(evaluation_id)

    if not evaluation or evaluation.status not in SCREENING_STATUSES or evaluation.validator_hotkey != screener_hotkey:
        logger.warning(f"Screener {screener_hotkey}: Invalid finish_screening call for evaluation {evaluation_id}")
        return

    agent = await get_agent_by_version(evaluation.version_id)

    if agent.status not in SCREENING_STATUSES:
        logger.warning(f"Invalid status for miner agent: expected {evaluation.status}, agent is set to {agent.status}")

    if errored:
        """Error evaluation and reset agent"""
        await asyncio.gather(
            update_evaluation_to_error(evaluation_id, reason),
            set_agent_status(
                version_id=agent.version_id,
                status=AgentStatus.awaiting_screening_1.value if evaluation.status == "screening_1" else AgentStatus.awaiting_screening_2.value
            )
        )
        
        logger.info(f"Screener {screener_hotkey}: Finishing screening {evaluation_id}: Errored with reason: {reason}")

async def handle_disconnect():
    pass