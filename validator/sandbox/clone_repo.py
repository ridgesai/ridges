import shutil
from pathlib import Path
from typing import Optional

from git import Repo

from shared.logging_utils import get_logger

logger = get_logger(__name__)

def clone_repo(path: Path, repo_name: str, base_commit: Optional[str] = None) -> Path:
    try:
        path.mkdir(parents=True, exist_ok=True)

        if path.exists() and path.is_dir():
            shutil.rmtree(path)
            logger.debug(f"Directory {path} has been removed.")

        repo = Repo.clone_from(f"https://github.com/{repo_name}.git", path)
        logger.debug(f"Repository cloned to {path}")
        if base_commit:
            repo.git.checkout(base_commit)
            logger.debug(f"Checked out base commit {base_commit}")
        return path
    except Exception as e:
        logger.exception(f"Failed to clone repository: {e}")
        raise