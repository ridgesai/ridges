"""Task for running agents in sandboxes."""

import os
import tempfile
import shutil
import httpx
from validator.sandbox.manager import SandboxManager
from validator.challenge.common import ChallengeTask
from shared.logging_utils import get_logger
from validator.config import RIDGES_API_URL, CHALLENGE_TIMEOUT

logger = get_logger(__name__)


async def run_agent_sandboxes(challenge: ChallengeTask):
    """Run agents in sandboxes and collect their outputs."""
    logger.info("Running sandboxes")

    # Create sandbox manager
    sbox_manager = SandboxManager()

    # Create a client with longer timeout for agent operations
    async with httpx.AsyncClient(timeout=CHALLENGE_TIMEOUT.total_seconds() * 2) as client:
        # Get list of agents from RIDGES API
        response = await client.get(f"{RIDGES_API_URL}/retrieval/agent-list", params={"type": "codegen"})
        response.raise_for_status()
        agents = response.json()["details"]["agents"]

        # Create and run a sandbox for each agent
        for agent in agents:
            try:
                # Download the agent code from Ridges API
                logger.info(f"Downloading agent code for agent {agent['agent_id']}")
                response = await client.get(
                    f"{RIDGES_API_URL}/retrieval/agent-file",
                    params={"agent_id": agent["agent_id"]},
                )
                response.raise_for_status()
                logger.info(f"Downloaded agent code for agent {agent['agent_id']}")

                # Create a temp directory for the agent code
                temp_dir = tempfile.mkdtemp()
                agent_file_path = os.path.join(temp_dir, "agent.py")

                # Save the Python file directly
                logger.info(f"Saving agent code to {agent_file_path}")
                with open(agent_file_path, "wb") as f:
                    f.write(response.content)
                logger.info(f"Saved agent code for agent {agent['agent_id']}")

                # Create a sandbox for the agent, that runs the agent code from the temp directory
                sbox = sbox_manager.add_sandbox(agent_id=agent["agent_id"], src_dir=temp_dir, repo_dir_path=agent_file_path)
                sbox.run_async(challenge)
            except Exception as e:
                logger.error(
                    f"Error configuring sandbox for agent {agent['agent_id']}: {e}"
                )

    # Wait for all sandboxes to finish
    logger.info("Waiting on sandboxes...")
    sbox_manager.wait_for_all_sandboxes()

    for agent_id, patch in sbox_manager.get_successful_agent_patches():
        logger.info(f"Agent {agent_id} generated patch: {patch}")

    sbox_manager.cleanup()
