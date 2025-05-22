from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from pprint import pformat
from typing import List

from simple_parsing.helpers.flatten import FlattenedAccess
from simple_parsing.helpers.serialization.serializable import FrozenSerializable
import sweagent
from sweagent.agent.agents import Agent
from sweagent.agent.agents import AgentArguments
from sweagent.agent.models import ModelArguments
from sweagent.environment.swe_env import EnvironmentArguments
from sweagent.environment.swe_env import SWEEnv
from sweagent.environment.utils import (
    get_data_path_name,
)
from sweagent.types import AgentInfo, TrajectoryStep

from agentao.helpers.classes import UnsolvedIssue, IssueSolution, MinerModelStats
from logging import Logger

@dataclass(frozen=True)
class ActionsArguments(FlattenedAccess, FrozenSerializable):
    """Run real-life actions (opening PRs, etc.) if we can solve the issue."""

    # Open a PR with the patch if we can solve the issue
    open_pr: bool = False
    # When working with local repository: Apply patch
    apply_patch_locally: bool = False
    # Option to be used with open_pr: Skip action if there are already commits claiming
    # to fix the issue. Please only set this to False if you are sure the commits are
    # not fixes or if this is your own repository!
    skip_if_commits_reference_issue: bool = True
    # OBSOLETE. Do not use, will raise error. Please specify --repo_path instead.
    push_gh_repo_url: str = ""

    def __post_init__(self):
        if self.push_gh_repo_url:
            msg = "push_gh_repo_url is obsolete. Use repo_path instead"
            raise ValueError(msg)


@dataclass(frozen=True)
class ScriptArguments(FlattenedAccess, FrozenSerializable):
    """Configure the control flow of the run.py script"""

    environment: EnvironmentArguments
    agent: AgentArguments
    actions: ActionsArguments
    # Only run instances that completely match this regex
    instance_filter: str = ".*"
    # Skip instances with existing trajectories
    skip_existing: bool = True
    # Suffix for the run name (used for example in trajectory directory naming)
    suffix: str = ""
    # Raise unhandled exceptions during the run (useful for debugging)
    raise_exceptions: bool = False
    # Dump the entire config to the log
    print_config: bool = True
    # Run the agent in CTF mode (SWE-agent: EnIGMA)
    ctf: bool = False

    @property
    def run_name(self) -> str:
        """Generate a unique name for this run based on the arguments."""
        model_name = self.agent.model.model_name.replace(":", "-")
        data_stem = get_data_path_name(self.environment.data_path)
        assert self.agent.config_file is not None  # mypy
        config_stem = Path(self.agent.config_file).stem

        temp = self.agent.model.temperature
        top_p = self.agent.model.top_p

        per_instance_cost_limit = self.agent.model.per_instance_cost_limit
        install_env = self.environment.install_environment

        return (
            f"{model_name}__{data_stem}__{config_stem}__t-{temp:.2f}__p-{top_p:.2f}"
            + f"__c-{per_instance_cost_limit:.2f}__install-{int(install_env)}"
            + (f"__{self.suffix}" if self.suffix else "")
        )

def create_script_arguments(
        model_name: str,
        unsolved_issue: UnsolvedIssue,
        instance_cost_limit: float
) -> ScriptArguments:
    swe_agent_root = Path(sweagent.__file__).parent.parent
    return ScriptArguments(
        environment=EnvironmentArguments(
            image_name="sweagent/swe-agent:latest",
            data_path=f"text://{unsolved_issue.desc}",
            repo_path=str(unsolved_issue.local_code_path),
            verbose=True,
            install_environment=True,
            environment_setup=str(unsolved_issue.env_setup_path)
        ),
        skip_existing=False,
        agent=AgentArguments(
            model=ModelArguments(
                model_name= model_name,
                per_instance_cost_limit=instance_cost_limit,
            ),
            config_file=Path(swe_agent_root / "config/default_from_url.yaml"),
        ),
        actions=ActionsArguments(
            open_pr=False,
            skip_if_commits_reference_issue=False,
            apply_patch_locally=True,
        ),
        print_config=True,
    )

def generate_code_patch(
    model_name: str, 
    unsolved_issue: UnsolvedIssue, 
    instance_cost_limit: float,
    logger: Logger
) -> IssueSolution:
    script_arguments = create_script_arguments(model_name, unsolved_issue, instance_cost_limit)

    env = SWEEnv(script_arguments.environment)
    observation, info = env.reset(0)

    agent = Agent("primary", script_arguments.agent)
    trajectories_dir = Path.cwd() / "trajectories"
    trajectories_dir.mkdir(exist_ok=True)

    logger.info("Running sweagent...")

    info: AgentInfo
    trajectory_steps: List[TrajectoryStep]

    start_time = time.time()
    info, trajectory_steps = agent.run(
        setup_args={"issue": getattr(env, "query", None), "files": [], "test_files": [], "tests": []},
        env=env,
        observation=observation,
        traj_dir=trajectories_dir,
        return_type="info_trajectory",
    )
    duration_s = time.time() - start_time

    if info.get("submission") is None:
        raise ValueError(f"SWE-agent failed to submit. Ran for {duration_s:.2f}s. Info: {pformat(info)}")

    readable_info = {
        k: (v if k not in ["edited_files30", "submission", "edited_files50"] else f"{v[:100]}...")
        for k, v in info.items()
    }
    logger.info(f"Finished running sweagent, ran for {duration_s:.2f}s. Received info: {pformat(readable_info)}")
    return IssueSolution(
        patch=info["submission"],
        model_stats=MinerModelStats.model_validate(
            info["model_stats"] | dict(duration_s=duration_s)
        ),
        exit_status=info["exit_status"],
    )