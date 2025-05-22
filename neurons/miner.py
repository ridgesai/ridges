# The MIT License (MIT)
# Copyright © 2023 Yuma Rao
# Copyright © 2023 Ridges
# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the "Software"), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:
# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.
from textwrap import dedent

from dotenv import load_dotenv

from ridges.miner.model_utils import get_envar_names_from_model_name, ALL_MODEL_NAMES, MODEL_CLASS_TO_ENVAR_NAMES
from neurons.constants import LOG_SESSION_CONTEXT

load_dotenv()

import argparse
import os
import tempfile
import time
from pathlib import Path
from typing import Dict
from typing import Tuple
import random

import yaml

import ridges
from ridges.base.miner import BaseMinerNeuron
from ridges.helpers.classes import UnsolvedIssue
from ridges.helpers.constants import EXAMPLE_PATCH
from ridges.helpers.helpers import clone_repo
from ridges.miner.generate_solution import generate_code_patch
from ridges.repo_environment import SUPPORTED_REPOS, REPO_TO_ENVIRONMENT_INFO
from ridges.helpers.clients import LogSessionContext, setup_logger

from logging import Logger

class MinerDefaults:
    MAX_INSTANCE_COST = 3.
    MODEL = "gpt-4o-mini"


class Miner(BaseMinerNeuron):
    """
    Your miner neuron class. You should use this class to define your miner's behavior. In particular, you should replace the forward function with your own logic. You may also want to override the blacklist and priority functions according to your needs.

    This class inherits from the BaseMinerNeuron class, which in turn inherits from BaseNeuron. The BaseNeuron class takes care of routine tasks such as setting up wallet, subtensor, metagraph, logging directory, parsing config, etc. You can override any of the methods in BaseNeuron if you need to customize the behavior.

    This class provides reasonable default behavior for a miner such as blacklisting unrecognized hotkeys, prioritizing requests based on stake, and forwarding requests to the forward function. If you need to define custom
    """

    def __init__(
        self, 
        config=None, 
        model: str = MinerDefaults.MODEL,
        max_instance_cost: float = MinerDefaults.MAX_INSTANCE_COST,
        use_mock_responses: bool = False,
    ):
        init_swe_agent(model)

        self.model_name = model
        self.max_instance_cost = max_instance_cost
        self.use_mock_responses = use_mock_responses

        super(Miner, self).__init__(config=config)

        hotkey = self.wallet.hotkey.ss58_address

        # Setup logging
        log_session_context = LogSessionContext(
            actor_id=hotkey,
            actor_type="miner",
            is_mainnet=self.subtensor.network == "finney",
            log_version=LOG_SESSION_CONTEXT,
            session_id=''.join(random.choices(''.join(map(chr, range(33,127))), k=8)),
            network=self.subtensor.network
        )

        self.logger: Logger = setup_logger(hotkey, log_session_context)

        self.logger.info(f"Using model: {self.model_name}")

    async def forward(
        self, synapse: ridges.protocol.CodingTask
    ) -> ridges.protocol.CodingTask:
        """
        Processes the incoming 'Dummy' synapse by performing a predefined operation on the input data.
        This method should be replaced with actual logic relevant to the miner's purpose.

        Args:
            synapse (template.protocol.Dummy): The synapse object containing the 'dummy_input' data.

        Returns:
            template.protocol.Dummy: The synapse object with the 'dummy_output' field set to twice the 'dummy_input' value.

        The 'forward' function is a placeholder and should be overridden with logic that is appropriate for
        the miner's intended operation. This method demonstrates a basic transformation of input data.
        """
        # if patch.txt exists return that
        self.logger.info("Starting miner forward pass...")
        self.logger.info(f"Received a request with repo: {synapse.repo}, problem statement: {synapse.problem_statement[:50]}...")

        current_dir = Path.cwd()

        try:
            repo = synapse.repo
            author_name, repo_name = repo.split("/")

            jobs_dir = Path("jobs")
            jobs_dir.mkdir(parents=True, exist_ok=True)
            self.logger.info(f"Using {jobs_dir.absolute()} as the directory for code repositories")

            self.logger.info(f"Cloning repo {repo}...")
            local_repo_dir = clone_repo(author_name, repo_name, current_dir.parent, logger=self.logger)
            self.logger.info(f"Finished cloning repo {repo}")

            if repo not in SUPPORTED_REPOS:
                raise ValueError(
                    f"Repo {repo} is not configured on miner. "
                    f"Please provide an environment setup file in REPO_TO_ENVIRONMENT_INFO"
                )

            repo_environment_info = REPO_TO_ENVIRONMENT_INFO[repo]
            with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w") as temp_env_file:
                yaml.dump(repo_environment_info.config_dict, temp_env_file)
                temp_env_file.flush()

                if self.use_mock_responses:
                    synapse.patch = EXAMPLE_PATCH
                    self.logger.info(f"Hotkey {self.wallet.hotkey.ss58_address} submitting dummy patch")
                else:
                    synapse.patch = generate_code_patch(
                        self.model_name,
                        UnsolvedIssue(
                            desc=synapse.problem_statement,
                            local_code_path=local_repo_dir,
                            env_setup_path=Path(temp_env_file.name)
                        ),
                        self.max_instance_cost,
                        self.logger
                    ).patch

            self.logger.info(f"Finished generating code patch for repo {synapse.repo}")

            self.logger.info(f"Exiting miner forward pass for repo {synapse.repo}")
            self.logger.debug(f"Returning patch: {synapse.patch}")
            return synapse
        except Exception:
            self.logger.exception("Error processing request")

    async def blacklist(
        self, synapse: ridges.protocol.CodingTask
    ) -> Tuple[bool, str]:
        """
        Determines whether an incoming request should be blacklisted and thus ignored. Your implementation should
        define the logic for blacklisting requests based on your needs and desired security parameters.

        Blacklist runs before the synapse data has been deserialized (i.e. before synapse.data is available).
        The synapse is instead contracted via the headers of the request. It is important to blacklist
        requests before they are deserialized to avoid wasting resources on requests that will be ignored.

        Args:
            synapse (template.protocol.Dummy): A synapse object constructed from the headers of the incoming request.

        Returns:
            Tuple[bool, str]: A tuple containing a boolean indicating whether the synapse's hotkey is blacklisted,
                            and a string providing the reason for the decision.

        This function is a security measure to prevent resource wastage on undesired requests. It should be enhanced
        to include checks against the metagraph for entity registration, validator status, and sufficient stake
        before deserialization of synapse data to minimize processing overhead.

        Example blacklist logic:
        - Reject if the hotkey is not a registered entity within the metagraph.
        - Consider blacklisting entities that are not validators or have insufficient stake.

        In practice, it would be wise to blacklist requests from entities that are not validators, or do not have
        enough stake. This can be checked via metagraph.S and metagraph.validator_permit. You can always attain
        the uid of the sender via a metagraph.hotkeys.index( synapse.dendrite.hotkey ) call.

        Otherwise, allow the request to be processed further.
        """

        if synapse.dendrite is None or synapse.dendrite.hotkey is None:
            self.logger.warning("Received a request without a dendrite or hotkey.")
            return True, "Missing dendrite or hotkey"

        uid = self.metagraph.hotkeys.index(synapse.dendrite.hotkey)
        if (
            not self.config.blacklist.allow_non_registered
            and synapse.dendrite.hotkey not in self.metagraph.hotkeys
        ):
            # Ignore requests from un-registered entities.
            self.logger.info(
                f"Blacklisting un-registered hotkey {synapse.dendrite.hotkey}"
            )
            return True, "Unrecognized hotkey"

        if self.config.blacklist.force_validator_permit:
            # If the config is set to force validator permit, then we should only allow requests from validators.
            if not self.metagraph.validator_permit[uid]:
                self.logger.warning(
                    f"Blacklisting a request from non-validator hotkey {synapse.dendrite.hotkey}"
                )
                return True, "Non-validator hotkey"

        self.logger.info(
            f"Not Blacklisting recognized hotkey {synapse.dendrite.hotkey}"
        )
        return False, "Hotkey recognized!"

    async def priority(self, synapse: ridges.protocol.CodingTask) -> float:
        """
        The priority function determines the order in which requests are handled. More valuable or higher-priority
        requests are processed before others. You should design your own priority mechanism with care.

        This implementation assigns priority to incoming requests based on the calling entity's stake in the metagraph.

        Args:
            synapse (template.protocol.Dummy): The synapse object that contains metadata about the incoming request.

        Returns:
            float: A priority score derived from the stake of the calling entity.

        Miners may receive messages from multiple entities at once. This function determines which request should be
        processed first. Higher values indicate that the request should be processed first. Lower values indicate
        that the request should be processed later.

        Example priority logic:
        - A higher stake results in a higher priority value.
        """
        if synapse.dendrite is None or synapse.dendrite.hotkey is None:
            self.logger.warning("Received a request without a dendrite or hotkey.")
            return 0.0
        
        caller_uid = self.metagraph.hotkeys.index(
            synapse.dendrite.hotkey
        )  # Get the caller index.
        priority = float(
            self.metagraph.S[caller_uid]
        )  # Return the stake as the priority.
        self.logger.info(
            f"Prioritizing {synapse.dendrite.hotkey} with value: {priority}"
        )
        return priority


def init_swe_agent(model_name: str) -> None:
    """Creates keys.cfg file from envars"""
    envar_names = get_envar_names_from_model_name(model_name)

    buffer = [f"{key}: '{os.environ[key]}'" for key in envar_names if key in os.environ]
    with open("SWE-agent/keys.cfg", "w") as f:
        f.write("\n".join(buffer) + "\n")


def construct_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=dedent(f"""We support the following models: OpenAI, Anthropic, Together, DeepSeek, Groq, Azure-hosted OpenAI, and local Ollama model running on localhost:11434. 
        Each model corresponds to the following envars (Azure is optional for OpenAIModel): {
        {   
            model_class.__name__:envar_names
            for model_class, envar_names in MODEL_CLASS_TO_ENVAR_NAMES.items()
        }}
        """)
    )
    parser.add_argument(
        "--model",
        help=f"The full list of supported models is: [{', '.join(ALL_MODEL_NAMES)}]",
        default=MinerDefaults.MODEL,
    )
    parser.add_argument("--max-instance-cost", type=float, default=MinerDefaults.MAX_INSTANCE_COST)
    parser.add_argument(
        "--use-mock-responses",
        action="store_true",
        default=False,
        help="Run miner in mock mode, returning a dummy patch"
    )
    parser.add_argument(
        "--miner-help",
        action="store_true",
        default=False,
        help="Print breakdown of supported models and miner args specific to the miner.py script"
    )
    return parser


def main(args_dict: Dict) -> None:
    with Miner(**args_dict) as miner:
        while True:
            time.sleep(5)

# This is the main function, which runs the miner.
if __name__ == "__main__":
    parser = construct_parser()
    args, _ = parser.parse_known_args()

    if args.miner_help:
        parser.print_help()
    else:
        args_dict = vars(args)
        args_dict.pop("miner_help")
        main(args_dict)
