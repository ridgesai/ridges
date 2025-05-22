"""
Script to test the miner `generate_code_patch` function used in the miner forward pass.

Uses a sample issue from the SWE-agent tutorial:
https://princeton-nlp.github.io/SWE-agent/usage/cl_tutorial
"""
import tempfile
from pathlib import Path

import yaml

from ridges.helpers.classes import UnsolvedIssue
from ridges.helpers.helpers import clone_repo
from ridges.miner.generate_solution import generate_code_patch
from ridges.repo_environment import REPO_TO_ENVIRONMENT_INFO
from neurons.miner import MinerDefaults

test_issue_desc = """I'm running missing_colon.py as follows:

division(23, 0)
but I get the following error:

  File "/Users/fuchur/Documents/24/git_sync/swe-agent-test-repo/tests/./missing_colon.py", line 4
    def division(a: float, b: float) -> float
                                             ^
SyntaxError: invalid syntax
"""

repo = "mwaskom/seaborn"
author_name, repo_name = repo.split("/")

from logging import Logger
logger = Logger("test_miner")

print(f"Cloning repo {repo}...")
local_repo_dir = clone_repo(author_name, repo_name, Path.cwd().parent, logger=logger)
print(f"Finished cloning repo {repo}")

repo_environment_info = REPO_TO_ENVIRONMENT_INFO[repo]

with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w") as temp_env_file:
    yaml.dump(repo_environment_info.config_dict, temp_env_file)
    temp_env_file.flush()

    test_unsolved_issue = UnsolvedIssue(
        desc=test_issue_desc,
        local_code_path=local_repo_dir,
        env_setup_path=Path(temp_env_file.name)
    )

    patch = generate_code_patch(
        MinerDefaults.MODEL,
        test_unsolved_issue,
        MinerDefaults.MAX_INSTANCE_COST,
        logger
    ).patch

    # Breakpoint for debugging/analysis
    import ipdb; ipdb.set_trace()

    