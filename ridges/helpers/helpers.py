import shutil
from pathlib import Path

import math
from git import Repo

from agentao.helpers.constants import PRICING_DATA_PER_MILLION_TOKENS

from logging import Logger

def clone_repo(author_name: str, repo_name: str, base_path: Path, logger: Logger) -> Path:
    """
    Clone a GitHub repository to a specified directory under 'repos' and return the path.

    :param author_name: GitHub username or organization name.
    :param repo_name: Repository name.
    :param base_path: Base path where the 'repos' directory will be created.
    :return: Path to the cloned repository.
    """
    try:
        repos_dir = base_path / "repos"
        repos_dir.mkdir(parents=True, exist_ok=True)

        clone_to_path = repos_dir / repo_name
        if clone_to_path.exists() and clone_to_path.is_dir():
            shutil.rmtree(clone_to_path)
            logger.debug(f"Directory {clone_to_path} has been removed.")

        Repo.clone_from(f"https://github.com/{author_name}/{repo_name}.git", clone_to_path)
        logger.debug(f"Repository cloned to {clone_to_path}")
        return clone_to_path
    except Exception:
        logger.exception(f"Failed to clone repository")
        raise


def calculate_price(model_name: str, input_tokens: int, output_tokens: int) -> float:
    pricing_dict = PRICING_DATA_PER_MILLION_TOKENS[model_name]
    input_price, output_price = pricing_dict["input"], pricing_dict["output"]
    return (input_tokens * input_price + output_tokens * output_price) / 1e6


def exponential_decay(N, x):
    """
    Outputs a value that approaches 1 as x approaches 0 and approaches 0 as x approaches or exceeds N.

    Parameters:
    - N (int or float): The threshold value.
    - x (int or float): The input value.

    Returns:
    - float: The output value.
    """
    if x >= N:
        return 0
    return math.exp(-x / (N - x))
