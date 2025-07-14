from api.src.backend.queries.statistics import get_top_agents
from api.src.utils.s3 import S3Manager
from loggers.logging_utils import get_logger

s3 = S3Manager()

logger = get_logger(__name__)

async def update_top_agent_code():
    """
    Updates the top agent code in the miner/agent.py file.
    """
    # Get top agent versionid
    top_agents = await get_top_agents(num_agents=1)
    top_agent = top_agents[0]
    logger.info(f"Updating top agent code with version id {top_agent.version_id}.")

    # Get code text from s3
    code_text = await s3.get_file_text(f"{top_agent.version_id}/agent.py")
    logger.info(f"Got code text from s3 for version id {top_agent.version_id}.")

    # Append warning on the top
    code_text = f"# This is the top agent. We encourage you to try to improve it, however purely copying and submitting it will result in an error.\n{code_text}"

    # Replace miner/agent.py with the new code
    with open("miner/agent.py", "w") as f:
        f.write(code_text)

    logger.info(f"Successfully updated top agent code with version id {top_agent.version_id}.")
