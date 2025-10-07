"""Utilities for computing diffs between files."""

import os
import tempfile
import subprocess



def get_file_diff(old_path, new_path):
    """Get the diff between two files, and report it as though it was a diff of the first file."""

    missing = []
    if not os.path.exists(old_path):
        missing.append(old_path)
    if not os.path.exists(new_path):
        missing.append(new_path)
    if missing:
        raise FileNotFoundError(f"File(s) not found for diffing: {', '.join(missing)}")
    
    # Use diff command
    result = subprocess.run(
        ["diff", "-u", old_path, new_path],
        capture_output=True,
        text=True
    )

    diff = result.stdout
        
    # Fix the header to use the same filename for both
    lines = diff.split("\n")
    if len(lines) >= 2:
        filename = os.path.basename(old_path)
        lines[0] = f"--- {filename}"
        lines[1] = f"+++ {filename}"
    
    return "\n".join(lines)



def validate_diff(diff, local_repo_path):
    """
    Validate if a diff string is valid and can be applied to a local repository.
    
    Args:
        diff: The diff string to validate
        local_repo_path: Path to the local repository
        
    Returns:
        tuple: (is_valid: bool, error_message: str or None)
    """
    
    # Write diff to temp file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".diff", delete=False) as f:
        f.write(diff)
        diff_file = f.name
    
    try:
        # Use git apply --check to validate without applying
        result = subprocess.run(
            ["git", "apply", "--check", diff_file],
            cwd=local_repo_path,
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            return True, None
        else:
            return False, result.stderr.strip()
            
    except Exception as e:
        return False, str(e)
    
    finally:
        os.unlink(diff_file)



def apply_diff(diff, local_repo_path):
    """
    Apply a diff string to files in the source directory.
    
    Args:
        diff: The diff string to apply
        local_repo_path: Path to the local repository
        
    Returns:
        tuple: (success: bool, error_message: str or None)
    """

    # Write diff to temp file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".diff", delete=False) as f:
        f.write(diff)
        diff_file = f.name
    
    try:
        # Use git apply to apply the diff
        result = subprocess.run(
            ["git", "apply", diff_file],
            cwd=local_repo_path,
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            return True, None
        else:
            return False, result.stderr.strip()
            
    except Exception as e:
        return False, str(e)
        
    finally:
        os.unlink(diff_file)