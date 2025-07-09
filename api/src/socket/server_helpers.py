import os
import httpx
import uuid
from datetime import datetime, timezone
import time

from api.src.utils.logging_utils import get_logger
from api.src.backend.entities import Evaluation, EvaluationRun, EvaluationStatus, SandboxStatus
from api.src.backend.queries.evaluations import (
    store_evaluation, 
    store_evaluation_run, 
    get_running_evaluation_by_validator_hotkey, 
    delete_evaluation_runs, 
)


logger = get_logger(__name__)

_commits_cache = None
_cache_time = 0

async def get_github_commits(history_length: int = 30) -> list[str]:
    """
    Get the previous commits from ridgesai/ridges.
    """
    global _commits_cache, _cache_time
    
    if _commits_cache and (time.time() - _cache_time) < 60:
        return _commits_cache

    headers = {"Accept": "application/vnd.github.v3+json"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"https://api.github.com/repos/ridgesai/ridges/commits?per_page={history_length}", headers=headers)
        response.raise_for_status()
        commits = response.json()
        _commits_cache = [commit["sha"] for commit in commits]
        _cache_time = time.time()
        return _commits_cache
    
async def get_relative_version_num(commit_hash: str, history_length: int = 30) -> int:
    """
    Get the previous commits from ridgesai/ridges.
    
    Returns:
        List of commit hashes (empty list if error)
    """
    try:
        headers = {"Accept": "application/vnd.github.v3+json"}
        
        # Add GitHub token if available for higher rate limits
        if token := os.getenv("GITHUB_TOKEN"):
            headers["Authorization"] = f"token {token}"
        
        commit_list = await get_github_commits(history_length)
        if commit_hash not in commit_list:
            logger.warning(f"Commit {commit_hash} not found in commit list")
            return -1
            
        return commit_list.index(commit_hash)
            
    except Exception as e:
        logger.error(f"Failed to get determine relative version number for commit {commit_hash}: {e}")
        return -1

async def upsert_evaluation_run(evaluation_run: dict) -> EvaluationRun:
    """
    Upsert an evaluation run into the database.
    """
    
    def parse_datetime(dt_str):
        """Parse datetime string to datetime object, return None if None or empty"""
        if not dt_str:
            return None
        if isinstance(dt_str, datetime):
            return dt_str
        return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
    
    evaluation_run_obj = EvaluationRun(
        run_id=evaluation_run["run_id"],
        evaluation_id=evaluation_run["evaluation_id"],
        swebench_instance_id=evaluation_run["swebench_instance_id"],
        status=SandboxStatus(evaluation_run["status"]),
        response=evaluation_run["response"],
        error=evaluation_run["error"],
        pass_to_fail_success=evaluation_run["pass_to_fail_success"],
        fail_to_pass_success=evaluation_run["fail_to_pass_success"],
        pass_to_pass_success=evaluation_run["pass_to_pass_success"],
        fail_to_fail_success=evaluation_run["fail_to_fail_success"],
        solved=evaluation_run["solved"],
        started_at=parse_datetime(evaluation_run["started_at"]),
        sandbox_created_at=parse_datetime(evaluation_run["sandbox_created_at"]),
        patch_generated_at=parse_datetime(evaluation_run["patch_generated_at"]),
        eval_started_at=parse_datetime(evaluation_run["eval_started_at"]),
        result_scored_at=parse_datetime(evaluation_run["result_scored_at"])
    )
    await store_evaluation_run(evaluation_run_obj)

    return evaluation_run_obj

async def create_evaluation(version_id: str, validator_hotkey: str) -> str:
    """
    Create a new evaluation in the database. Returns the evaluation id.
    """
    evaluation = Evaluation(
        evaluation_id=str(uuid.uuid4()),
        version_id=version_id,
        validator_hotkey=validator_hotkey,
        status=EvaluationStatus.waiting,
        terminated_reason=None,
        created_at=datetime.now(timezone.utc),
        started_at=None,
        finished_at=None,
        score=None
    )
    
    await store_evaluation(evaluation=evaluation)

    return evaluation.evaluation_id

async def reset_running_evaluations(validator_hotkey: str):
    """
    Reset all running evaluations for a validator. Essentially, add them back to the waiting queue.
    Before resetting, delete all associated evaluation runs since they will need to be remade.
    """

    evaluation = await get_running_evaluation_by_validator_hotkey(validator_hotkey)
    if evaluation:
        # Delete all associated evaluation runs first
        await delete_evaluation_runs(evaluation.evaluation_id)
        logger.info(f"Deleted evaluation runs for evaluation {evaluation.evaluation_id}")
        
        # Reset the evaluation to waiting status
        evaluation.status = EvaluationStatus.waiting
        evaluation.started_at = None
        await store_evaluation(evaluation)
        logger.info(f"Validator {validator_hotkey} had a running evaluation {evaluation.evaluation_id} before it disconnected. It has been reset to waiting.")
    else:
        logger.info(f"Validator {validator_hotkey} did not have a running evaluation before it disconnected. No evaluations have been reset.")
