import os
from typing import Optional
import httpx
import uuid
import os
from datetime import datetime, timezone
import time

from api.src.utils.logging_utils import get_logger
from api.src.db.operations import DatabaseManager
from api.src.db.sqlalchemy_models import EvaluationRun, Evaluation
from api.src.backend.queries.evaluations import store_evaluation, store_evaluation_run, get_running_evaluation_by_validator_hotkey, delete_evaluation_runs, store_evaluation, get_evaluation_by_evaluation_id
from api.src.backend.entities import Evaluation as NewEvaluation, EvaluationStatus

logger = get_logger(__name__)

db = DatabaseManager()

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

async def get_next_evaluation(validator_hotkey: str) -> Optional[Evaluation]:
    """
    Get the next evaluation for a validator. Returns None if no evaluation is found.
    """

    evaluation = await db.get_next_evaluation(validator_hotkey)

    return evaluation

async def get_agent_version_for_validator(version_id: str) -> dict:
    """
    Get the agent version for a given version id.
    Returns a dictionary to avoid Pydantic model UUID conversion issues.
    """

    agent_version = await db.get_agent_version(version_id)

    return agent_version

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
        status=evaluation_run["status"],
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
    await db.store_evaluation_run(evaluation_run_obj)

    return evaluation_run_obj

async def create_evaluation(version_id: str, validator_hotkey: str) -> str:
    """
    Create a new evaluation in the database. Returns the evaluation id.
    """
    evaluation = NewEvaluation(
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

async def start_evaluation(evaluation_id: str) -> Evaluation:
    """
    Start an evaluation in the database.
    """
    evaluation = await db.get_evaluation(evaluation_id)
    evaluation.status = EvaluationStatus.running
    evaluation.started_at = datetime.now(timezone.utc)
    await db.store_evaluation(evaluation)

    return evaluation

async def finish_evaluation(evaluation_id: str, errored: bool) -> Evaluation:
    """
    Finish an evaluation in the database.
    """
    
    evaluation = await get_evaluation_by_evaluation_id(evaluation_id)
    evaluation.status = EvaluationStatus.completed if not errored else EvaluationStatus.error
    evaluation.finished_at = datetime.now(timezone.utc)
    await db.store_evaluation(evaluation)

    return evaluation

async def reset_running_evaluations(validator_hotkey: str):
    """
    Reset all running evaluations for a validator. Essentially, add them back to the waiting queue.
    Before resetting, delete all associated evaluation runs since they will need to be remade.
    """

    evaluation = await db.get_running_evaluation_by_validator_hotkey(validator_hotkey)
    if evaluation:
        # Delete all associated evaluation runs first
        await db.delete_evaluation_runs(evaluation.evaluation_id)
        logger.info(f"Deleted evaluation runs for evaluation {evaluation.evaluation_id}")
        
        # Reset the evaluation to waiting status
        evaluation.status = EvaluationStatus.waiting
        evaluation.started_at = None
        await db.store_evaluation(evaluation)
        logger.info(f"Validator {validator_hotkey} had a running evaluation {evaluation.evaluation_id} before it disconnected. It has been reset to waiting.")
    else:
        logger.info(f"Validator {validator_hotkey} did not have a running evaluation before it disconnected. No evaluations have been reset.")

async def create_evaluations_for_validator(validator_hotkey: str) -> int:
    """
    Create evaluations for a validator. Returns the number of evaluations created.
    """

    try:
        num_evaluations_created = await db.create_evaluations_for_validator(validator_hotkey)
        logger.info(f"Created {num_evaluations_created} evaluations for validator {validator_hotkey}")
    except Exception as e:
        logger.error(f"Failed to create evaluations for validator {validator_hotkey}: {e}")
        return -1

    return num_evaluations_created
