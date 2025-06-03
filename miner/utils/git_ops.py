import os
import shutil
from git import Repo
import logging

def clone_and_checkout_repo(repository_name: str, commit_hash: str = None, base_dir: str = "/tmp/miner_repos") -> str:
    """
    Clones the given GitHub repository and checks out the specified commit or branch if provided.
    Returns the path to the cloned repo.
    """
    # Construct the GitHub URL
    github_url = f"https://github.com/{repository_name}.git"
    repo_dir = os.path.join(base_dir, repository_name.replace("/", "_"))

    # Clean up any previous clone
    if os.path.exists(repo_dir):
        shutil.rmtree(repo_dir)

    # Clone the repo
    repo = Repo.clone_from(github_url, repo_dir)

    # Checkout the commit or branch if provided
    if commit_hash:
        try:
            repo.git.checkout(commit_hash)
        except Exception as e:
            logging.warning(f"Could not checkout {commit_hash}: {e}. Using default branch instead.")
    # If no commit_hash, stay on default branch
    return repo_dir 