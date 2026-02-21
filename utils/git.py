"""Git utilities for managing repository operations."""

import os
import pathlib
import subprocess
import utils.logger as logger



def clone_repo(repo_url: str, target_dir: str) -> None:
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



def clone_local_repo_at_commit(local_repo_dir: str, commit_hash: str, target_dir: str) -> None:
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
    logger.debug(f"Cloning local repository from {local_repo_dir} to {target_dir}...")
    
    try:
        subprocess.run(
            ["git", "clone", abs_local_repo_dir, target_dir],
            capture_output=True,
            text=True,
            check=True
        )
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to clone local repository from {abs_local_repo_dir} to {target_dir}")
        logger.error(f"Git clone exit code: {e.returncode}")
        logger.error(f"Git clone stdout: {e.stdout or ''}")
        logger.error(f"Git clone stderr: {e.stderr or ''}")
        raise
    
    logger.debug(f"Cloned local repository from {local_repo_dir} to {target_dir}")

    # Checkout the specific commit
    logger.debug(f"Checking out commit {commit_hash} in {target_dir}...")

    try:
        subprocess.run(
            ["git", "checkout", commit_hash],
            capture_output=True,
            text=True,
            check=True,
            cwd=target_dir
        )
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to checkout commit {commit_hash} in {target_dir}")
        logger.error(f"Git checkout exit code: {e.returncode}")
        logger.error(f"Git checkout stdout: {e.stdout or ''}")
        logger.error(f"Git checkout stderr: {e.stderr or ''}")
        raise
    
    logger.debug(f"Checked out commit {commit_hash} in {target_dir}")

def reset_local_repo_to_commit(local_repo_dir: str, commit_hash: str, target_dir: str) -> None:
    """
    Reset a local repository to a specific commit and remove all future commits.
    This ensures that future commits cannot be accessed via git log or git show.
    
    Args:
        local_repo_dir: Path to the local repository
        commit_hash: The commit hash to reset to
        target_dir: Directory to reset the repository in
    """
    
    # Make sure the local repository path exists
    if not os.path.exists(local_repo_dir):
        logger.fatal(f"Local repository directory does not exist: {local_repo_dir}")

    # Reset the repository to the specific commit
    logger.debug(f"Resetting local repository to commit {commit_hash} in {target_dir}...")

    subprocess.run(
        ["git", "reset", "--hard", commit_hash],
        capture_output=True,
        text=True,
        check=True,
        cwd=target_dir
    )
    
    logger.debug(f"Reset local repository to commit {commit_hash} in {target_dir}")
    
    # Remove all future commits by:
    # 1. Delete all branch refs that point to commits after the target commit
    # 2. Update HEAD to point directly to the commit (detached HEAD)
    # 3. Prune unreachable objects and garbage collect
    
    logger.debug(f"Removing future commits from {target_dir}...")
    
    # Delete all branch refs (we'll use detached HEAD instead)
    # This removes references to future commits
    try:
        branches_result = subprocess.run(
            ["git", "branch"],
            capture_output=True,
            text=True,
            check=True,
            cwd=target_dir
        )
        # Parse branch names (remove leading * and whitespace)
        branches = []
        for line in branches_result.stdout.strip().split('\n'):
            line = line.strip()
            if line:
                # Remove leading * and whitespace
                branch = line.lstrip('*').strip()
                if branch:
                    branches.append(branch)
        
        for branch in branches:
            subprocess.run(
                ["git", "branch", "-D", branch],
                capture_output=True,
                text=True,
                check=False,  # Don't fail if branch doesn't exist
                cwd=target_dir
            )
    except subprocess.CalledProcessError:
        pass  # No branches to delete
    
    # Delete all remote refs (they might point to future commits)
    try:
        subprocess.run(
            ["git", "remote", "remove", "origin"],
            capture_output=True,
            text=True,
            check=False,  # Don't fail if remote doesn't exist
            cwd=target_dir
        )
    except Exception:
        pass
    
    # Delete all tag refs (they might point to future commits)
    try:
        tags_result = subprocess.run(
            ["git", "tag", "-l"],
            capture_output=True,
            text=True,
            check=True,
            cwd=target_dir
        )
        tags = [t.strip() for t in tags_result.stdout.strip().split('\n') if t.strip()]
        for tag in tags:
            subprocess.run(
                ["git", "tag", "-d", tag],
                capture_output=True,
                text=True,
                check=False,
                cwd=target_dir
            )
    except subprocess.CalledProcessError:
        pass  # No tags to delete
    
    # Set HEAD to point directly to the commit (detached HEAD state)
    # This ensures HEAD points to the exact commit, not a branch
    subprocess.run(
        ["git", "checkout", "--detach", commit_hash],
        capture_output=True,
        text=True,
        check=True,
        cwd=target_dir
    )
    
    # Prune unreachable objects and garbage collect to actually remove them
    # This physically deletes commits that are no longer reachable
    subprocess.run(
        ["git", "reflog", "expire", "--expire=now", "--all"],
        capture_output=True,
        text=True,
        check=False,  # May fail if no reflog exists
        cwd=target_dir
    )
    
    subprocess.run(
        ["git", "gc", "--prune=now", "--aggressive"],
        capture_output=True,
        text=True,
        check=True,
        cwd=target_dir
    )
    
    logger.debug(f"Removed future commits from {target_dir}")


def verify_commit_exists_in_local_repo(local_repo_dir: str, commit_hash: str) -> bool:
    """
    Verify that a specific commit exists in the repository.
    
    Args:
        local_repo_dir: Path to the local repository
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



def init_local_repo_with_initial_commit(local_repo_dir: str, commit_message: str = "Initial commit") -> None:
    """
    Initialize a Git repository in the given directory and make an initial commit with all the files in the directory.
    
    Args:
        directory: Path to the directory to initialize as a Git repo
        commit_message: Commit message for the initial commit (default: "Initial commit")
    """

    # Initialize git repository
    logger.debug(f"Initializing git repository in {local_repo_dir}")
    subprocess.run(
        ['git', 'init'],
        capture_output=True,
        text=True,
        check=True,
        cwd=local_repo_dir
    )
    logger.debug(f"Initialized git repository in {local_repo_dir}")

    # Add all files
    logger.debug(f"Adding all files in {local_repo_dir}")
    subprocess.run(
        ['git', 'add', '.'],
        capture_output=True,
        text=True,
        check=True,
        cwd=local_repo_dir
    )
    logger.debug(f"Added all files in {local_repo_dir}")
    
    # Make initial commit
    logger.debug(f"Making initial commit in {local_repo_dir}: {commit_message}")
    try:
        subprocess.run(
            ['git', 'commit', '-m', commit_message],
            capture_output=True,
            text=True,
            check=True,
            cwd=local_repo_dir
        )
        logger.debug(f"Made initial commit in {local_repo_dir}: {commit_message}")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to make initial commit in {local_repo_dir}")
        logger.error(f"Git commit exit code: {e.returncode}")
        logger.error(f"Git commit stdout: {e.stdout or ''}")
        logger.error(f"Git commit stderr: {e.stderr or ''}")
        raise



def reset_local_repo(local_repo_dir: str, commit_hash: str) -> None:
    """
    Resets the local repository to the specified commit hash.
    
    Args:
        local_repo_dir: Path to the local repository
        commit_hash: The commit hash to reset to
    """

    logger.info(f"Fetching repository at {local_repo_dir}...")
    subprocess.run(["git", "fetch"], text=True, check=True, cwd=local_repo_dir)
    logger.info(f"Fetched repository at {local_repo_dir}")

    logger.info(f"Resetting {local_repo_dir} to commit {commit_hash}...")
    subprocess.run(["git", "reset", "--hard", commit_hash], text=True, check=True, cwd=local_repo_dir)
    logger.info(f"Reset {local_repo_dir} to commit {commit_hash}")



def get_local_repo_commit_hash(local_repo_dir: str) -> str:
    """
    Get the commit hash of the current commit in the local repository.
    
    Args:
        local_repo_dir: Path to the local repository
    """
    
    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, cwd=local_repo_dir).stdout.strip()



COMMIT_HASH = get_local_repo_commit_hash(pathlib.Path(__file__).parent.parent)