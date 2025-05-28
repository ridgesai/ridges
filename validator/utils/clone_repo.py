import shutil
from pathlib import Path
from typing import Optional

from git import Repo

from fiber.logging_utils import get_logger

logger = get_logger(__name__)

def clone_repo(base_path: Path, repo_name: str, base_commit: Optional[str] = None) -> Path:
    """
    Clone a GitHub repository to a specified directory under 'repos' and return the path.
    """
    try:
        repos_dir = base_path / "repos"
        repos_dir.mkdir(parents=True, exist_ok=True)

        clone_to_path = repos_dir / repo_name
        if clone_to_path.exists() and clone_to_path.is_dir():
            shutil.rmtree(clone_to_path)
            logger.debug(f"Directory {clone_to_path} has been removed.")

        repo = Repo.clone_from(f"https://github.com/{repo_name}.git", clone_to_path)
        logger.debug(f"Repository cloned to {clone_to_path}")
        if base_commit:
            repo.git.checkout(base_commit)
            logger.debug(f"Checked out base commit {base_commit}")
        return clone_to_path
    except Exception as e:
        logger.exception(f"Failed to clone repository: {e}")
        raise
