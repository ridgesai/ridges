"""Utility functions for getting agent evaluation runs"""

from typing import List, Dict, Any
from swebench.harness.run_evaluation import load_swebench_dataset
from validator.sandbox.schema import AgentVersion
from validator.config import EASY_INSTANCES
from validator.utils.logging import get_logger

logger = get_logger(__name__)

def get_agent_evaluation_runs(agent_version: AgentVersion) -> List[Dict[str, Any]]:
    """Get evaluation runs for an agent version"""
    try:
        # Load SWE-bench instances (using easy instances for now)
        instances = load_swebench_dataset("SWE-bench/SWE-bench_Verified", "test", EASY_INSTANCES)
        
        # Convert to evaluation run format
        evaluation_runs = []
        for instance in instances:
            evaluation_run = {
                "instance_id": instance["instance_id"],
                "problem_statement": instance["problem_statement"],
                "repo": instance["repo"],
                "base_commit": instance["base_commit"],
                "run_id": f"{agent_version.miner_hotkey}_{agent_version.version_num}_{instance['instance_id']}",
            }
            evaluation_runs.append(evaluation_run)
        
        logger.info(f"Generated {len(evaluation_runs)} evaluation runs for agent {agent_version.miner_hotkey}")
        return evaluation_runs
        
    except Exception as e:
        logger.error(f"Failed to get evaluation runs for agent {agent_version.miner_hotkey}: {e}")
        return [] 