import os
from typing import Optional
import httpx
import uuid
from datetime import datetime

from api.src.utils.logging import get_logger
from api.src.db.operations import DatabaseManager
from api.src.utils.models import AgentVersionForValidator, EvaluationRun, Evaluation

logger = get_logger(__name__)

db = DatabaseManager()

def get_recent_commit_hashes(history_length: int = 30) -> list:
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
        
        with httpx.Client(timeout=10.0) as client:
            # Get last 30 commits
            response = client.get(f"https://api.github.com/repos/ridgesai/ridges/commits?per_page={history_length}", headers=headers)
            response.raise_for_status()
            commits = response.json()
            
            return [commit["sha"] for commit in commits]
            
    except Exception as e:
        logger.error(f"Failed to get commits: {e}")
        return []

def get_next_evaluation(validator_hotkey: str) -> Optional[Evaluation]:
    """
    Get the next evaluation for a validator. Returns None if no evaluation is found.
    """

    evaluation = db.get_next_evaluation(validator_hotkey)

    return evaluation

def get_agent_version_for_validator(version_id: str) -> AgentVersionForValidator:
    """
    Get the agent version for a given version id.
    """

    agent_version = db.get_agent_version(version_id)
    agent = db.get_agent(agent_version.agent_id)

    agent_version_for_validator = AgentVersionForValidator(
        version_id=agent_version.version_id,
        agent_id=agent_version.agent_id,
        version_num=agent_version.version_num,
        created_at=agent_version.created_at,
        score=agent_version.score,
        miner_hotkey=agent.miner_hotkey
    )
    return agent_version_for_validator

def upsert_evaluation_run(evaluation_run: dict):
    """
    Upsert an evaluation run into the database.
    """
    
    evaluation_run = EvaluationRun(
        run_id=evaluation_run["run_id"],
        evaluation_id=evaluation_run["evaluation_id"],
        swebench_instance_id=evaluation_run["swebench_instance_id"],
        response=evaluation_run["response"],
        error=evaluation_run["error"],
        pass_to_fail_success=evaluation_run["pass_to_fail_success"],
        fail_to_pass_success=evaluation_run["fail_to_pass_success"],
        pass_to_pass_success=evaluation_run["pass_to_pass_success"],
        fail_to_fail_success=evaluation_run["fail_to_fail_success"],
        solved=evaluation_run["solved"],
        started_at=evaluation_run["started_at"],
        finished_at=evaluation_run["finished_at"]
    )
    db.store_evaluation_run(evaluation_run)

def create_evaluation(version_id: str, validator_hotkey: str) -> str:
    """
    Create a new evaluation in the database. Returns the evaluation id.
    """
    
    evaluation_object = Evaluation(
        evaluation_id=str(uuid.uuid4()),
        version_id=version_id,
        validator_hotkey=validator_hotkey,
        status="waiting",
        terminated_reason=None,
        created_at=datetime.now(),
        started_at=None,
        finished_at=None
    )
    db.store_evaluation(evaluation_object)

    return evaluation_object.evaluation_id

def start_evaluation(evaluation_id: str):
    """
    Start an evaluation in the database.
    """
    
    evaluation = db.get_evaluation(evaluation_id)
    evaluation.status = "running"
    evaluation.started_at = datetime.now()
    db.store_evaluation(evaluation)

def finish_evaluation(evaluation_id: str, errored: bool):
    """
    Finish an evaluation in the database.
    """
    
    evaluation = db.get_evaluation(evaluation_id)
    evaluation.status = "completed" if not errored else "error"
    evaluation.finished_at = datetime.now()
    db.store_evaluation(evaluation)
