"""
TEMPORARY FILE - use to put logic that we need in the models folder that don't have a clear endpoints file to go into
"""

from api.src.backend.entities import AgentStatus, MinerAgent
from api.src.backend.queries.agents import set_agent_status
from api.src.utils.config import SCREENING_1_THRESHOLD, SCREENING_2_THRESHOLD


async def repair_agent_status():
    """Handles:
        - Screener disconnects
        - Validator disconnects
        - Platform restarts
    """
    pass

async def replace_old_agents(agent: MinerAgent):
    pass

async def update_agent_status(agent: MinerAgent):
    """Update agent status based on evaluation state - handles multi-stage screening"""
    
    # We use the database as the source of truth now. Fetch evaluations and then use that to determine how to update agent status

    return