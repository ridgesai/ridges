from dataclasses import dataclass
from datetime import datetime
import os
import random
import uuid
from pathlib import Path
from textwrap import dedent
from typing import Any, Dict, Final, List

from git import Optional, Repo
from jinja2 import Template

from validator.utils.clone_repo import clone_repo
from validator.config import AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_BUCKET_NAME

@dataclass
class GeneratedRegressionProblem:
    challenge_id: str
    repository_url: str
    commit_hash: Optional[str]
    problem_statement: str
    context_file_paths: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "challenge_id": self.challenge_id,
            "repository_url": self.repository_url,
            "commit_hash": self.commit_hash,
            "problem_statement": self.problem_statement,
            "context_file_paths": self.context_file_paths
        }

PROBLEM_STATEMENT_TEMPLATE_SWESMITH: Final[Template] = Template(
    dedent("""
    Here is a list of modified files:

    {% for file in affected_files %}
    Filename: {{ file.path }}
    ```python3
    {{ file.contents }}
    ```
    {% endfor %}
    ```
    
    Before modifying those files, all tests in this repository passed.
    After modifying those files, the following tests fail:
    {% for test in failing_tests %}
    {{ test }}
    {% endfor %}

    We want to make these tests pass.
    """)
)

@dataclass
class Bug:
    instance_id: str
    repo: str
    patch: str
    FAIL_TO_PASS: List[str]
    PASS_TO_PASS: List[str]
    created_at: datetime
    image_name: str
    base_commit: str

class Environment:
    """
    Placeholder for an execution environment.
    """
    def __init__(self, repo_name: str):
        self.repo_name = repo_name

    def create_bug(self) -> Bug:
        """
        Placeholder for creating a bug, which is a patch that causes one or more previously passing tests to fail.
        Will get theis data from a docker container running tests.
        """
        return Bug(
            instance_id=str(uuid.uuid4()),
            repo=self.repo_name,
            patch="""
                diff --git a/newfolder/main.py b/newfolder/main.py
                new file mode 100644
                index 0000000..df1dc68
                --- /dev/null
                +++ b/newfolder/main.py
                @@ -0,0 +1 @@
                +print('Hello World')
            """,
            FAIL_TO_PASS=["test_hello_world"],
            PASS_TO_PASS=["test_hello_world"],
            created_at=datetime.now().isoformat(),
            image_name="swesmith/Instagram__MonkeyType.70c3acf6",
            base_commit="70c3acf6",
        )

def create_execution_environment(repo_name: str) -> Environment:
    """
    Creates an execution environment for a given repo.
    Inside, we can generate mutations, validate which induce bugs, and then return the bug.
    Here, a bug is defined as a patch that causes one or more previously passing tests to fail.

    TODO: This is a placeholder until we can run a docker container with the repo and the dependencies installed.
    """
    return Environment(repo_name)

async def create_regression_challenge() -> GeneratedRegressionProblem:
    """
    Creates a codegen challenge using swesmith's bug generation framework.

    Steps:
    1. Pick a random repo from the supported repos
    2. Create an execution environment
    3. Generate a bug, extracting a patch and list of failing tests induced by the patch
    4. Upload the patch to a new repo hosted on S3
    5. Generate a problem statement which contains the failing tests
    """
    supported_repos = ["swesmith/Instagram__MonkeyType.70c3acf6"]

    repo_name = random.choice(supported_repos)

    # Create execution environment
    environment = create_execution_environment(repo_name)

    # Generate a bug and extract the patch and list of failing tests
    bug = environment.create_bug()

    # Clone the repo locally and apply the patch
    repo_path = clone_repo(Path.cwd() / "repos", repo_name, bug.base_commit)
    repo = Repo(repo_path)
    
    # Apply patch from string
    try:
        repo.git.apply('--stdin', input=bug.patch)
    except Exception as e:
        raise Exception(f"Failed to apply patch: {str(e)}")

    # Get the list of files that were affected by the patch, relative to the repo root
    affected_files = [os.path.relpath(item.a_path, repo_path) for item in repo.index.diff(None)]

    # Upload the repo to S3
    problem_id = str(uuid.uuid4())
    repo.git.commit("-m", f"Bug {problem_id}", author="validator@ridges.ai")
    s3_url = f"https://{AWS_ACCESS_KEY_ID}:{AWS_SECRET_ACCESS_KEY}@s3.amazonaws.com/{AWS_BUCKET_NAME}/{problem_id}"
    repo.create_remote('s3', s3_url)
    repo.git.push('s3', 'HEAD:main')

    # Generate a problem statement which contains the failing tests
    problem_statement = PROBLEM_STATEMENT_TEMPLATE_SWESMITH.render(
        affected_files=affected_files,
        failing_tests=bug.FAIL_TO_PASS
    )

    return GeneratedRegressionProblem(
        challenge_id=problem_id,
        repository_url=s3_url,
        commit_hash=bug.base_commit,
        problem_statement=problem_statement,
        context_file_paths=affected_files,
    )
