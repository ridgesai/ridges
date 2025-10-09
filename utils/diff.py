"""Utilities for computing diffs between files."""

import os
import tempfile
import subprocess
import utils.logger as logger

from typing import Tuple, Optional



def get_file_diff(old_path, new_path) -> str:
    """
    Gets the diff between two files.
    
    Args:
        old_path: The path to the old file
        new_path: The path to the new file
        
    Returns:
        The diff between the two files, expressed as a diff of the old file, as a string.
    """

    missing = []
    if not os.path.exists(old_path):
        missing.append(old_path)
    if not os.path.exists(new_path):
        missing.append(new_path)
    if missing:
        logger.fatal(f"File(s) not found for diff: {', '.join(missing)}")
    
    # Use diff command
    result = subprocess.run(
        ["diff", "-u", old_path, new_path],
        capture_output=True,
        text=True
    )

    # Check if the diff was generated successfully
    # `diff -u` return codes:
    #     0: no differences
    #     1: differences
    #     2: error
    if result.returncode != 0 and result.returncode != 1:
        logger.fatal(f"Failed to get diff between {old_path} and {new_path}: {result.stderr.strip()}")

    # Get the diff
    diff = result.stdout

    # Fix the header to use the same filename for both
    lines = diff.split("\n")
    if len(lines) >= 2:
        filename = os.path.basename(old_path)
        lines[0] = f"--- {filename}"
        lines[1] = f"+++ {filename}"
    
    return "\n".join(lines)



def validate_diff_for_local_repo(diff, local_repo_dir) -> Tuple[bool, Optional[str]]:
    """
    Validates if a diff string is valid and can be applied to a local repository.
    
    Args:
        diff: The diff string to validate
        local_repo_dir: The local repository directory
        
    Returns:
        (is_valid: bool, error_message: Optional[str])
    """
    
    # Write diff to temp file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".diff", delete=False) as f:
        f.write(diff)
        diff_file = f.name
    
    # Use `git apply --check` to validate without applying
    result = subprocess.run(
        ["git", "apply", "--check", diff_file],
        cwd=local_repo_dir,
        capture_output=True,
        text=True
    )

    # Delete the temp file
    os.unlink(diff_file)
    
    # Check if the diff was applied successfully
    if result.returncode == 0:
        return True, None
    else:
        return False, result.stderr.strip()



def apply_diff_to_local_repo(diff, local_repo_dir) -> None:
    """
    Applies a diff string to files in the source directory.
    
    Args:
        diff: The diff string to apply
        local_repo_dir: The local repository directory
    """

    # Write diff to temp file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".diff", delete=False) as f:
        f.write(diff)
        diff_file = f.name
    
    # Use `git apply` to apply the diff
    result = subprocess.run(
        ["git", "apply", diff_file],
        cwd=local_repo_dir,
        capture_output=True,
        text=True
    )

    # Delete the temp file
    os.unlink(diff_file)

    # Check if the diff was applied successfully
    if result.returncode != 0:
        logger.fatal(f"Failed to apply diff to {local_repo_dir}: {result.stderr.strip()}")