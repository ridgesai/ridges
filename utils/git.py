"""Git utilities for managing repository operations."""

import os
import subprocess
import utils.logger as logger



def clone_repo(repo_url, target_dir) -> None:
    """
    Clone a repository from a URL into the target directory.
    
    Args:
        repo_url: URL of the repository to clone (e.g., https://github.com/owner/repo.git)
        target_dir: Directory to clone the repository into
    """
    
    logger.info(f"Cloning repository from {repo_url} to {target_dir}")
 
    result = subprocess.run(["git", "clone", repo_url, target_dir])
    if result.returncode != 0:
        logger.fatal(f"Failed to clone repository from {repo_url} to {target_dir}: {result.returncode}")
    
    logger.info(f"Successfully cloned repository from {repo_url} to {target_dir}")



def clone_local_repo_at_commit(local_repo_dir, commit_hash, target_dir) -> None:
    """
    Clone a local repository at a specific commit into the target directory.
    
    Args:
        local_repo_dir: Path to the local repository 
        commit_hash: The commit hash to clone from
        target_dir: Directory to clone the repository into
    """
    
    # Make sure the local repository path exists
    if not os.path.exists(local_repo_dir):
        logger.fatal(f"Local repository directory does not exist: {local_repo_dir}")
    
    # Convert to absolute path to avoid issues with relative paths
    abs_local_repo_dir = os.path.abspath(local_repo_dir)
    
    # Clone the local repository directly to the target directory
    logger.info(f"Cloning local repository from {local_repo_dir} to {target_dir}...")
    
    result = subprocess.run(
        ["git", "clone", abs_local_repo_dir, target_dir],
        capture_output=True,
        text=True,
        check=True
    )
    
    logger.info(f"Cloned local repository from {local_repo_dir} to {target_dir}")

    # Checkout the specific commit
    logger.info(f"Checking out commit {commit_hash} in {target_dir}...")

    result = subprocess.run(
        ["git", "checkout", commit_hash],
        capture_output=True,
        text=True,
        check=True,
        cwd=target_dir
    )
    
    logger.info(f"Checked out commit {commit_hash} in {target_dir}")



def verify_commit_exists_in_local_repo(local_repo_dir, commit_hash) -> bool:
    """
    Verify that a specific commit exists in the repository.
    
    Args:
        local_repo_dir: Path to the repository
        commit_hash: The commit hash to verify
        
    Returns:
        bool: True if commit exists, False otherwise
    """
    
    # Make sure the local repository directory exists
    if not os.path.exists(local_repo_dir):
        return False
    
    # Use `git cat-file -e` to verify that the commit exists
    result = subprocess.run(
        ["git", "cat-file", "-e", commit_hash],
        capture_output=True,
        text=True,
        cwd=local_repo_dir
    )

    # `git cat-file -e` return codes:
    #     0: commit exists
    #     non-zero: commit does not exist
    return result.returncode == 0



def init_local_repo_with_initial_commit(local_repo_dir, commit_message="Initial commit") -> None:
    """
    Initialize a Git repository in the given directory and make an initial commit with all the files in the directory.
    
    Args:
        directory: Path to the directory to initialize as a Git repo
        commit_message: Commit message for the initial commit (default: "Initial commit")
    """

    # Initialize git repository
    logger.info(f"Initializing git repository in {local_repo_dir}")
    subprocess.run(
        ['git', 'init'],
        capture_output=True,
        text=True,
        check=True,
        cwd=local_repo_dir
    )
    logger.info(f"Initialized git repository in {local_repo_dir}")

    # Add all files
    logger.info(f"Adding all files in {local_repo_dir}")
    subprocess.run(
        ['git', 'add', '.'],
        capture_output=True,
        text=True,
        check=True,
        cwd=local_repo_dir
    )
    logger.info(f"Added all files in {local_repo_dir}")
    
    # Make initial commit
    logger.info(f"Making initial commit in {local_repo_dir}: {commit_message}")
    subprocess.run(
        ['git', 'commit', '-m', commit_message],
        capture_output=True,
        text=True,
        check=True,
        cwd=local_repo_dir
    )
    logger.info(f"Made initial commit in {local_repo_dir}: {commit_message}")