from git import Repo

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