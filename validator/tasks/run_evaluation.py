"""Task for running agents in sandboxes."""

import asyncio
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from validator.config import RIDGES_API_URL, validator_hotkey
from validator.sandbox.manager import SandboxManager
from validator.sandbox.schema import AgentVersion, EvaluationRun
from validator.utils.logging import get_logger

if TYPE_CHECKING:
    from validator.socket.websocket_app import WebsocketApp

logger = get_logger(__name__)

async def _download_agent_code(agent_version: AgentVersion, agent_file: Path) -> None:
    """Download agent code from the API and save to file"""
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            logger.info(f"Downloading agent code for version {agent_version.version_id}")
            
            response = await client.get(
                f"{RIDGES_API_URL}/retrieval/agent-version-file",
                params={"version_id": agent_version.version_id},
            )
            response.raise_for_status()
            
            # Write the agent code to file
            with open(agent_file, "wb") as f:
                f.write(response.content)
                
            logger.info(f"Successfully downloaded agent code to {agent_file}")
            
    except httpx.HTTPError as e:
        logger.error(f"HTTP error downloading agent code: {e}")
        raise Exception(f"Failed to download agent code: HTTP {e}")
    except Exception as e:
        logger.error(f"Unexpected error downloading agent code: {e}")
        raise Exception(f"Failed to download agent code: {e}")

async def run_evaluation(websocket_app: "WebsocketApp", evaluation_id: str, agent_version: AgentVersion):
    """Run evaluation for a specific agent version"""
    logger.info(f"Starting evaluation {evaluation_id} for agent {agent_version.miner_hotkey}")
    
    sandbox_manager = SandboxManager(websocket_app)
    errored = False
    
    try:
        # Send start-evaluation message
        await websocket_app.send({
            "event": "start-evaluation",
            "evaluation_id": evaluation_id,
        })
        
        # Create evaluation tasks for each SWE-bench instance
        await _create_evaluation_tasks(sandbox_manager, evaluation_id, agent_version)
        
        # Wait for all evaluations to complete
        logger.info("Waiting for all evaluations to complete...")
        await sandbox_manager.wait_for_all_sandboxes()
        
        logger.info(f"Evaluation {evaluation_id} completed successfully")
        
    except asyncio.CancelledError:
        logger.info("Evaluation cancelled - cleaning up resources")
        errored = True
        sandbox_manager.cleanup(force_cancel=True)
        raise
    except Exception as e:
        logger.error(f"Error during evaluation: {e}", exc_info=True)
        errored = True
    finally:
        # Clean up resources (gentle cleanup for normal completion)
        sandbox_manager.cleanup(force_cancel=errored)
        
        # Send completion message if connected
        if websocket_app.ws is not None:
            try:
                await websocket_app.send({
                    "event": "finish-evaluation",
                    "evaluation_id": evaluation_id,
                    "errored": errored,
                })
            except Exception as e:
                logger.error(f"Failed to send finish-evaluation message: {e}")
        else:
            logger.info("Websocket disconnected - skipping finish-evaluation message")

async def _create_evaluation_tasks(sandbox_manager: SandboxManager, evaluation_id: str, agent_version: AgentVersion):
    """Create evaluation tasks for agent version"""
    from validator.sandbox.constants import AGENTS_BASE_DIR
    from validator.utils.get_agent_version_runs import get_agent_evaluation_runs
    
    # Get evaluation runs for this agent version
    evaluation_runs = get_agent_evaluation_runs(agent_version)
    if not evaluation_runs:
        logger.warning(f"No evaluation runs found for agent {agent_version.miner_hotkey}")
        return
    
    logger.info(f"Creating {len(evaluation_runs)} evaluation tasks for agent {agent_version.miner_hotkey}")
    
    # Create agent directory
    agent_dir = AGENTS_BASE_DIR / agent_version.miner_hotkey / str(agent_version.version_num)
    agent_dir.mkdir(parents=True, exist_ok=True)
    
    # Download agent code from API
    agent_file = agent_dir / "agent.py"
    try:
        await _download_agent_code(agent_version, agent_file)
        logger.info(f"Downloaded agent code for {agent_version.miner_hotkey} version {agent_version.version_num}")
    except Exception as e:
        logger.error(f"Failed to download agent code for {agent_version.miner_hotkey}: {e}")
        raise
    
    # Create evaluation tasks
    for evaluation_run_data in evaluation_runs:
        try:
            # Create evaluation run
            evaluation_run = EvaluationRun(
                run_id=str(uuid.uuid4()),
                evaluation_id=evaluation_id,
                validator_hotkey=validator_hotkey.ss58_address,
                swebench_instance_id=evaluation_run_data.get("instance_id"),
                status="started",
                started_at=datetime.now(timezone.utc),
            )
            
            # Create sandbox
            sandbox = sandbox_manager.create_sandbox(evaluation_run, agent_dir)
            
            # Start sandbox task immediately (they will run concurrently)
            sandbox._task = asyncio.create_task(
                _run_sandbox_with_error_handling(sandbox_manager, sandbox, evaluation_run_data)
            )
            
        except Exception as e:
            logger.error(f"Failed to create sandbox for evaluation run {evaluation_run_data.get('instance_id')}: {e}")
    
    logger.info(f"Created {len(sandbox_manager.sandboxes)} evaluation tasks")

async def _run_sandbox_with_error_handling(sandbox_manager: SandboxManager, sandbox, evaluation_run_data):
    """Run sandbox with proper error handling"""
    try:
        await sandbox_manager.run_sandbox(sandbox, evaluation_run_data)
        logger.info(f"Sandbox {sandbox.evaluation_run.run_id} completed successfully")
    except Exception as e:
        logger.error(f"Sandbox {sandbox.evaluation_run.run_id} failed: {e}")
        # Update evaluation run with error
        sandbox.evaluation_run.error = str(e)
        sandbox.evaluation_run.status = "result_scored"
        sandbox.evaluation_run.result_scored_at = datetime.now(timezone.utc)
        sandbox.evaluation_run.solved = False
        
        # Send update
        try:
            await sandbox_manager.websocket_app.send({
                "event": "upsert-evaluation-run",
                "evaluation_run": sandbox.evaluation_run.to_dict(),
            })
        except Exception as send_error:
            logger.error(f"Failed to send error update: {send_error}")
