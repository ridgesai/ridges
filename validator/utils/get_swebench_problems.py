"""Utility functions for getting agent evaluation runs"""

from typing import List
import httpx
from swebench.harness.run_evaluation import load_swebench_dataset
from validator.sandbox.schema import SwebenchProblem
from validator.config import RIDGES_API_URL, SCREENER_MODE
from loggers.logging_utils import get_logger

logger = get_logger(__name__)

async def get_swebench_problems(evaluation_id: str) -> List[SwebenchProblem]:
    """Get evaluation runs for an agent version"""
    try:
        instance_ids = await get_evaluation_set_instances(evaluation_id)
        instances = load_swebench_dataset("SWE-bench/SWE-bench_Verified", "test", instance_ids)
        
        problems = [SwebenchProblem(
            instance_id=instance["instance_id"],
            problem_statement=instance["problem_statement"],
            repo=instance["repo"],
            base_commit=instance["base_commit"],
        ) for instance in instances]
        
        logger.info(f"Generated {len(problems)} problems for evaluation {evaluation_id}")
        return problems
        
    except Exception as e:
        logger.error(f"Failed to get evaluation runs for evaluation {evaluation_id}: {e}")
        return [] 

async def get_evaluation_set_instances(evaluation_id: str) -> List[str]:
    """Get evaluation set instances for a given evaluation_id and eval_type"""
    eval_type = "screener" if SCREENER_MODE else "validator"
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{RIDGES_API_URL}/retrieval/evaluation-set", params={"evaluation_id": evaluation_id, "type": eval_type})
        response.raise_for_status()
        return response.json()