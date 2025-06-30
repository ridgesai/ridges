import shutil
from pathlib import Path
from typing import Optional
import time

from git import Repo
from git.exc import GitCommandError, InvalidGitRepositoryError

from validator.utils.logging import get_logger

logger = get_logger(__name__)

def clone_repo(path: Path, repo_name: str, base_commit: Optional[str] = None) -> Path:
    try:
        path.mkdir(parents=True, exist_ok=True)

        # If the directory already looks like a git repository, just reuse it.
        if (path / ".git").exists():
            try:
                repo = Repo(path)
                logger.debug(f"Reusing existing repository at {path}")
            except InvalidGitRepositoryError:
                # Directory exists but isn't a valid repo – clean it and reclone once
                logger.warning(f"Existing directory at {path} is not a valid git repo; recloning…")
                shutil.rmtree(path, ignore_errors=True)
                repo = Repo.clone_from(f"https://github.com/{repo_name}.git", path)
                logger.debug(f"Repository cloned to {path}")
        else:
            # Empty or non-existent directory → fresh clone
            if any(path.iterdir()):
                logger.debug(f"Directory {path} exists but is not a git repo; recloning…")
                shutil.rmtree(path, ignore_errors=True)
                path.mkdir(parents=True, exist_ok=True)
            try:
                repo = Repo.clone_from(f"https://github.com/{repo_name}.git", path)
                logger.debug(f"Repository cloned to {path}")
            except GitCommandError as gce:
                if "already exists and is not an empty directory" in str(gce):
                    # The directory is being cloned by another worker, or we raced. Reuse.
                    logger.info(f"Clone raced for {repo_name}; reusing existing directory {path}.")
                    # Wait briefly for .git to materialize if another clone is still in progress
                    for _ in range(5):
                        if (path / ".git").exists():
                            break
                        time.sleep(1)
                    try:
                        repo = Repo(path)
                    except InvalidGitRepositoryError:
                        # Still not a git repo – treat as empty clone (should be rare)
                        logger.warning(f"Path {path} still not a valid repo after race; retrying clone once…")
                        shutil.rmtree(path, ignore_errors=True)
                        path.mkdir(parents=True, exist_ok=True)
                        repo = Repo.clone_from(f"https://github.com/{repo_name}.git", path)
                else:
                    raise

        if base_commit:
            try:
                repo.git.checkout(base_commit)
                logger.debug(f"Checked out base commit {base_commit}")
            except GitCommandError as gce:
                # Handle possible stale lock file
                lock_file = path / ".git" / "index.lock"
                if lock_file.exists():
                    logger.warning(f"Removing stale index.lock at {lock_file}")
                    lock_file.unlink(missing_ok=True)
                    repo.git.checkout(base_commit)
                else:
                    raise
        return path
    except Exception as e:
        logger.exception(f"Failed to clone repository: {e}")
        raise