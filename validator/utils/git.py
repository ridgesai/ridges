"""Git utilities for managing repository operations."""

import os
import subprocess
import shutil
import tempfile
from validator.utils.logger import debug, info, warn, error



def clone_repo(repo_url, target_dir):
    """
    Clone a repository from a URL into the target directory.
    
    Args:
        repo_url: URL of the repository to clone (e.g., https://github.com/owner/repo.git)
        target_dir: Directory to clone the repository into
        
    Returns:
        tuple: (success: bool, error_message: str or None)
    """
    
    try:
        debug(f"[GIT] Cloning repository from {repo_url} to {target_dir}")
 
        result = subprocess.run(["git", "clone", repo_url, target_dir])
        if result.returncode != 0:
            return False, f"Failed to clone repository: {result.returncode}"
        
        debug(f"[GIT] Successfully cloned repository to {target_dir}")
 
        return True, None
        
    except Exception as e:
        return False, f"Failed to clone repository from {repo_url} to {target_dir}: {str(e)}"



def clone_local_repo_at_commit(repo_path, commit_hash, target_dir):
    """
    Clone a local repository at a specific commit into the target directory.
    
    Args:
        repo_path: Path to the local repository 
        commit_hash: The commit hash to checkout
        target_dir: Directory to clone the repository into
        
    Returns:
        tuple: (success: bool, error_message: str or None)
    """
    
    if not os.path.exists(repo_path):
        return False, f"Repository path does not exist: {repo_path}"
    
    if not os.path.exists(target_dir):
        return False, f"Target directory does not exist: {target_dir}"
    
    # Convert to absolute path to avoid issues with relative paths in temp directories
    abs_repo_path = os.path.abspath(repo_path)
    
    try:
        # Create a temporary directory for the clone operation
        with tempfile.TemporaryDirectory() as temp_clone_dir:
            temp_repo_path = os.path.join(temp_clone_dir, "repo")
            
            # Clone the repository
            debug(f"[GIT] Cloning repository from {abs_repo_path} to {temp_repo_path}")
            result = subprocess.run(
                ["git", "clone", abs_repo_path, temp_repo_path],
                capture_output=True,
                text=True,
                cwd=temp_clone_dir
            )
            
            if result.returncode != 0:
                return False, f"Failed to clone repository: {result.stderr}"
            
            # Checkout the specific commit
            debug(f"[GIT] Checking out commit {commit_hash}")
            result = subprocess.run(
                ["git", "checkout", commit_hash],
                capture_output=True,
                text=True,
                cwd=temp_repo_path
            )
            
            if result.returncode != 0:
                return False, f"Failed to checkout commit {commit_hash}: {result.stderr}"
            
            # Copy all files from the cloned repo to the target directory
            # (excluding .git directory to save space)
            debug(f"[GIT] Copying repository files to {target_dir}")
            for item in os.listdir(temp_repo_path):
                if item == ".git":
                    continue
                    
                src_path = os.path.join(temp_repo_path, item)
                dst_path = os.path.join(target_dir, item)
                
                if os.path.isdir(src_path):
                    shutil.copytree(src_path, dst_path, dirs_exist_ok=True)
                else:
                    shutil.copy2(src_path, dst_path)
            
            debug(f"[GIT] Successfully cloned repository at commit {commit_hash} to {target_dir}")
            return True, None
            
    except Exception as e:
        return False, f"Git operation failed: {str(e)}"



def verify_commit_exists(repo_path, commit_hash):
    """
    Verify that a specific commit exists in the repository.
    
    Args:
        repo_path: Path to the repository
        commit_hash: The commit hash to verify
        
    Returns:
        bool: True if commit exists, False otherwise
    """
    
    if not os.path.exists(repo_path):
        return False
    
    try:
        result = subprocess.run(["git", "cat-file", "-e", commit_hash], capture_output=True, text=True, cwd=repo_path)
        return result.returncode == 0
        
    except Exception:
        return False



def init_repo_with_initial_commit(directory, commit_message="Initial commit"):
    """
    Initialize a git repository in the given directory and make an initial commit with all files in the directory.
    
    Args:
        directory: Path to the directory to initialize as a git repo
        commit_message: Commit message for the initial commit (default: "Initial commit")
        
    Returns:
        bool: True if successful, False otherwise
    """

    try:
        # Initialize git repository
        debug(f"[GIT] Initializing git repository in {directory}")
        subprocess.run(['git', 'init'], capture_output=True, text=True, check=True, cwd=directory)
        debug(f"[GIT] Initialized git repository in {directory}")

        # Add all files
        debug(f"[GIT] Adding all files in {directory}")
        subprocess.run(['git', 'add', '.'], capture_output=True, text=True, check=True, cwd=directory)
        debug(f"[GIT] Added all files in {directory}")
        
        # Make initial commit
        debug(f"[GIT] Making initial commit: {commit_message}")
        subprocess.run(['git', 'commit', '-m', commit_message], capture_output=True, text=True, check=True, cwd=directory)
        debug(f"[GIT] Made initial commit: {commit_message}")
        
        return True
        
    except subprocess.CalledProcessError as e:
        warn(f"[GIT] Git command failed: {e}")
        warn(f"[GIT] Command output: {e.stdout}")
        warn(f"[GIT] Command error: {e.stderr}")

        return False
    
    except Exception as e:
        warn(f"[GIT] Failed to initialize git repository: {e}")

        return False