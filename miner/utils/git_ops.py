import os
import shutil
from git import Repo
import logging
import uuid


def get_git_url(repo: str) -> str:
    """
    Returns a full git URL. If the input is a repo name (e.g., 'user/repo'),
    assumes GitHub. If it's a full URL, returns as-is.
    """
    if "://" in repo or repo.startswith("git@"):
        return repo
    else:
        return f"https://github.com/{repo}.git"


def clone_and_checkout_repo(repository: str, commit_hash: str = None, base_dir: str = "/tmp/miner_repos") -> str:
    """
    Clones the given repository (GitHub shorthand or full URL) and checks out the specified commit or branch if provided.
    Returns the path to the cloned repo.
    """
    git_url = get_git_url(repository)
    # Use a safe directory name and append a unique id
    unique_id = str(uuid.uuid4())
    repo_dir = os.path.join(base_dir, f"{repository.replace('/', '_').replace(':', '_')}_{unique_id}")

    # Clean up any previous clone (shouldn't be needed, but for safety)
    if os.path.exists(repo_dir):
        shutil.rmtree(repo_dir)

    # Ensure base_dir exists
    os.makedirs(base_dir, exist_ok=True)

    # Clone the repo
    repo = Repo.clone_from(git_url, repo_dir)

    # Checkout the commit or branch if provided
    if commit_hash:
        try:
            repo.git.checkout(commit_hash)
        except Exception as e:
            logging.warning(f"Could not checkout {commit_hash}: {e}. Using default branch instead.")
    # If no commit_hash, stay on default branch
    return repo_dir 