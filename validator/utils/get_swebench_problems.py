"""Utility functions for getting agent evaluation runs"""

from typing import List, Dict, Any
from swebench.harness.run_evaluation import load_swebench_dataset
from validator.sandbox.schema import AgentVersion, SwebenchProblem
from validator.config import EASY_INSTANCES, SCREENER_INSTANCES, SCREENER_MODE
from validator.utils.logging import get_logger

logger = get_logger(__name__)

def get_swebench_problems(agent_version: AgentVersion) -> List[SwebenchProblem]:
    """Get evaluation runs for an agent version"""
    try:
        if SCREENER_MODE:
            instances = load_swebench_dataset("SWE-bench/SWE-bench_Verified", "test", SCREENER_INSTANCES)
        else:
            instances = load_swebench_dataset("SWE-bench/SWE-bench_Verified", "test", EASY_INSTANCES)
        
        problems: List[SwebenchProblem] = []
        for instance in instances:
            problem = SwebenchProblem(
                instance_id=instance["instance_id"],
                problem_statement=instance["problem_statement"],
                repo=instance["repo"],
                base_commit=instance["base_commit"],
            )
            problems.append(problem)
        
        logger.info(f"Generated {len(problems)} problems for agent {agent_version.miner_hotkey}")
        return problems
        
    except Exception as e:
        logger.error(f"Failed to get evaluation runs for agent {agent_version.miner_hotkey}: {e}")
        return [] 