from dataclasses import dataclass
from datetime import datetime
import json
import os
import random
import shutil
import uuid
from pathlib import Path
from textwrap import dedent
from typing import Any, Dict, Final, List
from shared.logging_utils import get_logger

from git import Optional, Repo
import requests
from jinja2 import Template

from validator.utils.clone_repo import clone_repo
from validator.challenge import RegressionChallenge

logger = get_logger(__name__)




PROBLEM_STATEMENT_TEMPLATE_SWESMITH: Final[Template] = Template(
    dedent(
        """
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
    """
    )
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
    
    def create_docker_execution_environment(self) -> None:
        """
        Creates a docker container with the repo and the dependencies installed.
        Inside, we can generate mutations, validate which induce bugs, and then return the bug.
        Here, a bug is defined as a patch that causes one or more previously passing tests to fail.
        """
        pass

    def generate_bugs(self) -> None:
        """
        Generates bugs for the repo.
        """
        pass

    def collect_bugs(self) -> None:
        """
        Collects bugs into a single json for validation.
        """
        pass

    def validate_bugs(self) -> None:
        """
        Validates the bugs.
        """
        pass

    def get_random_bug(self) -> Bug:
        """
        Gets a random bug from the bugs json.
        """
        pass

    def create_bug(self) -> Bug:
        """
        Placeholder for creating a bug, which is a patch that causes one or more previously passing tests to fail.
        Will get this data from a docker container running tests.
        """

        # Get json from ../SWE-smith/logs/task_insts/monkeytype_pytest.json
        with open("../SWE-smith/logs/task_insts/monkeytype_pytest.json", "r") as f:
            data = json.load(f)

        # Get the first bug
        bug = random.choice(data)
        logger.info(
            f"Found bug: {bug['instance_id']} with {len(bug['FAIL_TO_PASS'])} failing tests"
        )

        return Bug(
            instance_id=bug["instance_id"],
            repo=bug["repo"],
            patch=bug["patch"],
            FAIL_TO_PASS=bug["FAIL_TO_PASS"],
            PASS_TO_PASS=bug["PASS_TO_PASS"],
            created_at=bug["created_at"],
            image_name=bug["image_name"],
            base_commit=bug["base_commit"],
        )


async def create_next_regression_challenge(validator_hotkey: str) -> RegressionChallenge:
    """
    Creates a codegen challenge using swesmith's bug generation framework.

    Steps:
    1. Pick a random repo from the supported repos
    2. Create an execution environment
    3. Generate a bug, extracting a patch and list of failing tests induced by the patch
    4. Upload the patch to a new repo hosted on S3 or Gitea
    5. Generate a problem statement which contains the failing tests
    """
    supported_repos = ["swesmith/Instagram__MonkeyType.70c3acf6"]

    repo_name = random.choice(supported_repos)

    environment = Environment(repo_name)
    # environment.create_docker_execution_environment()
    # environment.generate_bugs()
    # environment.collect_bugs()
    # environment.validate_bugs()
    # bug = environment.get_random_bug()

    bug = environment.create_bug()

    repo_path = clone_repo(Path.cwd(), repo_name, bug.base_commit)
    repo = Repo(repo_path)

    # Apply patch from string
    try:
        import subprocess

        # Use subprocess to pipe the patch string directly to git apply
        process = subprocess.Popen(
            ["git", "apply"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=repo.working_dir,
            text=True,
        )
        stdout, stderr = process.communicate(input=bug.patch)

        if process.returncode != 0:
            raise Exception(f"git apply failed: {stderr}")
    except Exception as e:
        raise Exception(f"Failed to apply patch: {str(e)}")

    # Get the list of files that were affected by the patch, relative to the repo root
    affected_files = [
        os.path.relpath(item.a_path, repo_path) for item in repo.index.diff(None)
    ]

    # Make a new repo with new history and upload it
    problem_id = str(uuid.uuid4())
    shutil.rmtree(repo_path / ".git")
    repo = Repo.init(repo_path)
    repo.git.add(".")
    repo.git.commit("-m", f"Initial commit", author="Validator <validator@ridges.ai>")

    logger.info(f"Uploading repo to Gitea: {repo_path}")
    repository_url = upload_repo_to_gitea(repo_path, problem_id)

    # Clean up local files
    shutil.rmtree(repo_path, ignore_errors=True)

    # Generate a problem statement which contains the failing tests
    problem_statement = PROBLEM_STATEMENT_TEMPLATE_SWESMITH.render(
        affected_files=affected_files, failing_tests=bug.FAIL_TO_PASS
    )

    return RegressionChallenge(
        challenge_id=problem_id,
        repository_url=repository_url,
        commit_hash=bug.base_commit,
        problem_statement=problem_statement,
        context_file_paths=affected_files,
        validator_hotkey=validator_hotkey
    )


def upload_repo_to_gitea(repo_path: Path, problem_id: str) -> str:
    """
    Create a repository on Gitea and upload the code there.

    Args:
        repo_path: Path to the git repository to upload
        problem_id: Unique identifier for the problem

    Returns:
        URL of the created Gitea repository
    """
    gitea_url = os.environ.get("RIDGES_GIT_URL", "https://git.ridges.ai")
    gitea_token = os.environ["RIDGES_GIT_TOKEN"]
    gitea_org = os.environ.get("RIDGES_GIT_ORG", "regression-challenges")

    logger.info(
        f"Creating repository on Gitea: {gitea_url}/{gitea_org}/challenge-{problem_id}"
    )
    # Create repository via Gitea API
    api_url = f"{gitea_url}/api/v1/orgs/{gitea_org}/repos"
    headers = {
        "Authorization": f"token {gitea_token}",
        "Content-Type": "application/json",
    }

    repo_data = {
        "name": f"challenge-{problem_id}",
        "description": f"Regression challenge {problem_id}",
        "private": False,
        "auto_init": False,
    }
    response = requests.post(api_url, headers=headers, json=repo_data)
    if response.status_code not in (201, 409):  # 409 if repo already exists
        raise Exception(
            f"Failed to create Gitea repository: {response.status_code} {response.text}"
        )

    # Get the clone URL
    repo_url = f"{gitea_url}/{gitea_org}/challenge-{problem_id}.git"
    logger.info(f"Repository URL: {repo_url}")

    # Push the repository to Gitea
    repo = Repo(repo_path)
    try:
        # Add the Gitea remote
        remote_name = "gitea"
        if remote_name in [r.name for r in repo.remotes]:
            repo.delete_remote(remote_name)

        # Create remote with authentication token in URL
        auth_url = repo_url.replace("https://", f"https://oauth2:{gitea_token}@")
        remote = repo.create_remote(remote_name, auth_url)

        # Push all refs to Gitea
        logger.info(f"Pushing to Gitea: {remote_name}")
        remote.push(refspec="refs/*:refs/*", force=True)

    except Exception as e:
        logger.warning(f"Failed to push to Gitea: {str(e)}")
        # If push fails, we still return the URL as the repo was created

    return repo_url
