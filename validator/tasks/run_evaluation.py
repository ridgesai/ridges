"""Task for running agents in sandboxes."""

import asyncio
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from ddtrace import tracer

import httpx

from validator.config import RIDGES_API_URL, SCREENER_MODE, validator_hotkey
from validator.sandbox.manager import SandboxManager
from validator.sandbox.schema import AgentVersion, EvaluationRun
from validator.sandbox.constants import AGENTS_BASE_DIR
from validator.utils.get_swebench_problems import get_swebench_problems
from loggers.logging_utils import get_logger

if TYPE_CHECKING:
    from validator.socket.websocket_app import WebsocketApp

logger = get_logger(__name__)

@tracer.wrap(resource="run-evaluation")
async def run_evaluation(websocket_app: "WebsocketApp", evaluation_id: str, agent_version: AgentVersion):
    """Run evaluation for a specific agent version"""
    logger.info(f"Starting evaluation {evaluation_id} for agent {agent_version.miner_hotkey}")
    
    sandbox_manager = SandboxManager(websocket_app)
    errored = False
    
    try:
        await websocket_app.send({"event": "start-evaluation", "evaluation_id": evaluation_id})
        
        problems = await get_swebench_problems(evaluation_id)
        if not problems:
            logger.warning(f"No problems found for agent {agent_version.miner_hotkey}")
            return
        
        agent_dir = AGENTS_BASE_DIR / agent_version.miner_hotkey / str(agent_version.version_num)
        agent_dir.mkdir(parents=True, exist_ok=True)
        
        # Download agent code
        agent_file = agent_dir / "agent.py"
        async with httpx.AsyncClient(timeout=300) as client:
            logger.info(f"Downloading agent code for version {agent_version.version_id}")
            response = await client.get(
                f"{RIDGES_API_URL}/retrieval/agent-version-file",
                params={"version_id": agent_version.version_id},
            )
            response.raise_for_status()
            agent_file.write_bytes(response.content)
        
        # Create evaluation runs and sandboxes
        for problem in problems:
            evaluation_run = EvaluationRun(
                run_id=str(uuid.uuid4()),
                evaluation_id=evaluation_id,
                validator_hotkey=validator_hotkey.ss58_address if not SCREENER_MODE else os.getenv("AWS_INSTANCE_ID"),
                swebench_instance_id=problem.instance_id,
                status="started",
                started_at=datetime.now(timezone.utc),
            )
            
            await sandbox_manager.create_sandbox(evaluation_run, problem, agent_dir)
        
        await sandbox_manager.run_all_sandboxes()
        logger.info(f"Evaluation {evaluation_id} completed successfully")
        
    except Exception as e:
        logger.error(f"Error during evaluation: {e}", exc_info=True)
        errored = True
    finally:
        sandbox_manager.cleanup(force_cancel=errored)
        
        if websocket_app.ws:
            try:
                await websocket_app.send({
                    "event": "finish-evaluation",
                    "evaluation_id": evaluation_id,
                    "errored": errored,
                })
            except Exception as e:
                logger.error(f"Failed to send finish-evaluation message: {e}")
