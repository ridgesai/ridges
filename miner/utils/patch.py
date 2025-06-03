from git import Repo
import tempfile
import os

def generate_patch(repo_path: str) -> str:
    """
    Generates a git patch (diff) of all changes in the repo at repo_path.
    Returns the patch as a string.
    """
    repo = Repo(repo_path)
    # Stage all changes (new files, modifications, deletions)
    repo.git.add(A=True)
    # Generate the diff against HEAD (the original commit)
    patch = repo.git.diff('HEAD')
    return patch 


def apply_patch(repo_path: str, patch: str):
    """
    Applies a git patch (diff) to the repo at repo_path.
    The patch should be a string in unified diff format.
    """
    repo = Repo(repo_path)
    temp_patch_file = None
    try:
        # Write patch to a temporary file
        with tempfile.NamedTemporaryFile(delete=False, mode='w', suffix='.patch') as f:
            f.write(patch)
            temp_patch_file = f.name
        # Apply the patch
        repo.git.apply(temp_patch_file)
    except Exception as e:
        raise RuntimeError(f"Failed to apply patch: {e}")
    finally:
        if temp_patch_file and os.path.exists(temp_patch_file):
            os.remove(temp_patch_file) 