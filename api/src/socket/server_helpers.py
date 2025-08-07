import os
import json
import subprocess
import httpx
from pathlib import Path

from loggers.logging_utils import get_logger

logger = get_logger(__name__)

SERVER_COMMIT_HASH = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode("utf-8").strip()

# Path for storing commits cache file
COMMITS_CACHE_FILE = Path("api/.commits_cache.json")

async def fetch_and_store_commits(history_length: int = 30):
    """Fetch commits from GitHub API and store them to file. Called once at startup."""
    try:
        headers = {"Accept": "application/vnd.github.v3+json"}
        
        # Add GitHub token if available for higher rate limits
        if token := os.getenv("GITHUB_TOKEN"):
            headers["Authorization"] = f"token {token}"
            
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"https://api.github.com/repos/ridgesai/ridges/commits?per_page={history_length}", 
                headers=headers
            )
            response.raise_for_status()
            commits = response.json()
            commit_list = [commit["sha"] for commit in commits]
            
            # Store to file
            COMMITS_CACHE_FILE.write_text(json.dumps(commit_list))
            logger.info(f"Stored {len(commit_list)} commits to cache file")
            return commit_list
            
    except Exception as e:
        logger.error(f"Failed to fetch and store commits: {e}")
        return []

def get_cached_commits() -> list[str]:
    """Load commits from cache file."""
    try:
        if COMMITS_CACHE_FILE.exists():
            return json.loads(COMMITS_CACHE_FILE.read_text())
        else:
            logger.warning("Commits cache file not found")
            return []
    except Exception as e:
        logger.error(f"Failed to load commits from cache: {e}")
        return []

async def get_github_commits(history_length: int = 30) -> list[str]:
    """Get the previous commits from ridgesai/ridges. Now reads from cached file."""
    commits = get_cached_commits()
    if not commits:
        logger.warning("No cached commits found, falling back to API call")
        return await fetch_and_store_commits(history_length)
    return commits
    
async def get_relative_version_num(commit_hash: str, history_length: int = 30) -> int:
    """Get the relative version number for a commit hash."""
    try:
        commit_list = await get_github_commits(history_length)
        if commit_hash not in commit_list:
            logger.warning(f"Commit {commit_hash} not found in commit list")
            return -1
            
        return commit_list.index(commit_hash)
            
    except Exception as e:
        logger.error(f"Failed to get determine relative version number for commit {commit_hash}: {e}")
        return -1
