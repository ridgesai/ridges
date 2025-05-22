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
import os

from dotenv import load_dotenv

from ridges.utils.load_sample_generated_problems import load_sample_problems
from ridges.validator.github.git_handler import GitHubOpenIssue
from ridges.validator.graders.helpers import preprocess_patch, run_tests

load_dotenv()

import argparse
import random
import time
from datetime import timedelta
from pathlib import Path
from typing import *
from dataclasses import asdict
import uuid

import numpy as np
from aiohttp import BasicAuth, ClientSession

from ridges.base.validator import BaseValidatorNeuron, TaskType
from ridges.helpers.classes import GeneratedProblemStatement, IssueSolution
from ridges.helpers.clients import LogContext
from ridges.helpers.constants import SUPPORTED_VALIDATOR_MODELS
from ridges.helpers.helpers import clone_repo, exponential_decay
from ridges.protocol import CodingTask
from ridges.repo_environment import SUPPORTED_SWEBENCH_REPOS
from ridges.utils.uids import check_uid_availability
from ridges.validator.generate_problem import create_problem_statements
from ridges.validator.graders.abstract_grader import MinerSubmission
from ridges.validator.graders.trueskill_grader import TrueSkillGrader, MockTrueSkillGrader
from ridges.validator.github.git_handler import GitHubIssueHandler, PrMetadata
from neurons.constants import LLM_EVAL_MULT, PROCESS_TIME_MULT, ValidatorDefaults
from neurons.constants import UPLOAD_ISSUE_ENDPOINT


# TODO: Also check if token is valid
IS_RUNNING_OPEN_ISSUES = "GITHUB_TOKEN" in os.environ


class Validator(BaseValidatorNeuron):
    """
    Your validator neuron class. You should use this class to define your validator's behavior. In particular, you should replace the forward function with your own logic.

    This class inherits from the BaseValidatorNeuron class, which in turn inherits from BaseNeuron. The BaseNeuron class takes care of routine tasks such as setting up wallet, subtensor, metagraph, logging directory, parsing config, etc. You can override any of the methods in BaseNeuron if you need to customize the behavior.

    This class provides reasonable default behavior for a validator such as keeping a moving average of the scores of the miners and using them to set weights at the end of each epoch. Additionally, the scores are reset for new hotkeys at the end of each epoch.
    """

    def __init__(
        self,
        config=None,
        model: str = ValidatorDefaults.MODEL,
        miner_request_timeout: int = ValidatorDefaults.CODINGTASK_TIMEOUT_MINS,
        use_mock_responses: bool = False,
    ):
        super(Validator, self).__init__(config=config)

        self.logger.info("load_state()")
        self.load_state()

        self.model_name = model
        self.miner_request_timeout_mins = miner_request_timeout
        self.use_mock_responses = use_mock_responses

        if self.use_mock_responses:
            self.grader = MockTrueSkillGrader(logger=self.logger)
        else:
            self.grader = TrueSkillGrader(logger=self.logger)

        # TODO: Make it mockable
        if IS_RUNNING_OPEN_ISSUES:
            self.github_handler = GitHubIssueHandler()

    async def calculate_rewards(
        self,
        repo: str,
        problem: GeneratedProblemStatement,
        issue_solutions: List[IssueSolution],
        # These are in seconds
        miner_hotkeys: List[str],
        process_times: List[float],
        forward_pass_id: str,
    ) -> np.ndarray:
        """
        Validate the responses from the miners. This function should score the responses and return a list of rewards for each miner.
        """
        miner_subs = [MinerSubmission(
            repo=repo,
            problem=problem,
            solution=issue_solution,
            miner_hotkey=hk,
        ) for issue_solution, hk in zip(issue_solutions, miner_hotkeys)]

        # LLM evaluation of patch
        llm_evals = np.array(self.grader.grade(miner_subs, forward_pass_id))
        
        # Response time score
        response_time_scores = [exponential_decay(self.miner_request_timeout_mins * 60, t) for t in process_times]
        for hk, rt, rts in zip(miner_hotkeys, process_times, response_time_scores):
            self.logger.info(f"Response time for miner {hk} is {rt} seconds -> score={rts}", extra=asdict(LogContext(
                log_type="lifecycle",
                event_type="response_score",
                additional_properties={
                    "response_time": rt, 
                    "miner_hotkey": hk, 
                    "response_time_score": rts,
                    "forward_pass_id": forward_pass_id,
                }
            )))
        response_times = np.array(response_time_scores)

        # Compute the final score
        final_scores = []
        for llm_eval, response_time in zip(llm_evals, response_times):
            if llm_eval == 0.0:
                final_scores.append(ValidatorDefaults.NO_RESPONSE_MIN)
            else:
                final_scores.append(LLM_EVAL_MULT*llm_eval + PROCESS_TIME_MULT*response_time)

        # Check if for each solution, whether the patch is the same as another miner submitted patch.
        # If so, then the score is 0.0
        for i, solution in enumerate(issue_solutions):
            for j, other_solution in enumerate(issue_solutions):
                if i != j and solution.patch == other_solution.patch:
                    final_scores[i] = ValidatorDefaults.NO_RESPONSE_MIN

        return np.array(final_scores)

    def validate_test_statuses(
        self,
        test_outcomes_before: Dict[str, bool],
        test_outcomes_after: Dict[str, bool],
        required_tests: List[str]
    ) -> bool:
        """
        Checks whether
            1. All previously passing tests are passing
            2. All required tests are passing
        """
        return \
            all(test_outcomes_after[test_name] for test_name in required_tests) and \
            all(
                test_outcomes_after[test_name]
                for test_name, did_test_pass in test_outcomes_before.items() if did_test_pass
            )

    def calculate_organic_rewards(self):
        rewards = self.github_handler.assign_rewards()

        rewards_vec = np.zeros(len(self.metagraph.uids))

        for miner_hotkey, pr_score in rewards.items():
            try:
                uid = self.metagraph.hotkeys.index(miner_hotkey)
                rewards_vec[uid] = pr_score
            except:
                continue

        self.update_scores(
            rewards=rewards_vec,
            uids=self.metagraph.uids.tolist(),
            task_type=TaskType.OPEN_ISSUE,
        )

    # TODO: Add more fields once components of scoring are named
    async def upload_solution(
            self,
            problem_statement: str,
            responses: List[IssueSolution],
            rewards_list: List[float],
            hotkeys: List[str],
    ):
        """
        Upload the closed issue to the data endpoint.
        """
        response_patches = [response.patch for response in responses]

        keypair = self.dendrite.keypair
        hotkey = keypair.ss58_address
        signature = f"0x{keypair.sign(hotkey).hex()}"
        try:
            async with ClientSession() as session:
                # TODO: Add how long it takes to upload the issue
                payload = [{
                    "problem_statement": problem_statement,
                    "solution_patch": response_patch,
                    "score": response_score,
                    "miner_hotkey": miner_hotkey,
                } for
                    response_patch,
                    response_score,
                    miner_hotkey
                    in zip(response_patches, rewards_list, hotkeys)
                ]
                async with session.post(
                    url=UPLOAD_ISSUE_ENDPOINT,
                    auth=BasicAuth(hotkey, signature),
                    json=payload,
                ) as response:
                    response.raise_for_status()
                    _result = await response.json()
        except Exception as e:
            self.logger.exception(f"Error uploading closed issue: {e}")

    async def forward(self):
        """
        Validator forward pass. Consists of:
        - Generating the query
        - Querying the miners
        - Getting the responses
        - Rewarding the miners
        - Updating the scores
        """
        forward_pass_id = str(uuid.uuid4())

        self.logger.add_forward_pass_context(forward_pass_id)

        self.logger.debug("Starting forward pass...")

        miner_uids = [
            uid for uid in range(len(self.metagraph.S))
            if check_uid_availability(self.metagraph, uid, self.config.neuron.vpermit_tao_limit)
        ]
        self.logger.info(f"Found {len(miner_uids)} miner UIDs: {miner_uids}")

        if len(miner_uids) > ValidatorDefaults.MAX_MINERS_PER_PROBLEM:
            miner_uids = random.sample(miner_uids, ValidatorDefaults.MAX_MINERS_PER_PROBLEM)
            self.logger.info(
                f"Subsampling {ValidatorDefaults.MAX_MINERS_PER_PROBLEM} uids from list of {len(miner_uids)}. Subsampled miner UIDs: {miner_uids}"
            )

        if len(miner_uids) == 0:
            self.logger.info("No miners available to query. Exiting forward pass...")
            return

        axons = [self.metagraph.axons[uid] for uid in miner_uids]

        self.logger.info(f"Current step={self.step}...")

        current_dir = Path.cwd()
        repo = random.choice(SUPPORTED_SWEBENCH_REPOS)

        author_name, repo_name = repo.split("/")

        self.logger.debug(f"Cloning repo {repo}...")
        local_repo_dir = clone_repo(author_name, repo_name, current_dir.parent, logger=self.logger)
        self.logger.debug(f"Finished cloning repo {repo}")

        num_problems_to_gen = 1
        if self.use_mock_responses:
            problems: List[GeneratedProblemStatement] = [random.choice(load_sample_problems())]
        else:
            problems: List[GeneratedProblemStatement] = create_problem_statements(
                self.model_name, repo, local_repo_dir, num_problems_to_gen, ValidatorDefaults.INGESTION_HEURISTICS, self.logger
            )

        problem: GeneratedProblemStatement = problems[0]

        self.logger.info(f"Problem statement is: {problem.problem_statement[:50]}...", extra=asdict(LogContext(
            log_type="lifecycle",
            event_type="question_generated",
            additional_properties={
                "question_text": problem.problem_statement, 
                "question_id": problem.problem_uuid, 
                "forward_pass_id": forward_pass_id,
            }
        )))

        self.logger.info(f"Sending task {problem.problem_uuid} to miners, ...")

        responses: List[CodingTask] = await self.dendrite(
            axons=axons,
            synapse=CodingTask(
                repo=repo,
                problem_statement=problem.problem_statement,
                patch=None,
            ),
            deserialize=False,
            timeout=timedelta(minutes=self.miner_request_timeout_mins).total_seconds(),
        )
        
        for r in responses:
            # Only record the submission if there is actually a patch
            if r.patch not in [None, ""]:
                self.logger.info(f"Received responses from miners for task {problem.problem_uuid}", extra=asdict(LogContext(
                    log_type="lifecycle",
                    event_type="miner_submitted",
                    additional_properties={
                        "miner_hotkey": r.axon.hotkey, 
                        "question_id": problem.problem_uuid, 
                        "patch": r.patch,
                        "response_time": r.dendrite.process_time,
                        "forward_pass_id": forward_pass_id,
                    }
                )))

        working_miner_uids: List[int] = []
        finished_responses: List[IssueSolution] = []
        process_times: List[float] = []

        self.logger.debug("Checking which received patches are valid...")

        for response in responses:
            if not response:
                self.logger.info(f"Miner with hotkey {response.axon.hotkey} did not give a response")
            elif response.patch in [None, ""] or not response.axon or not response.axon.hotkey:
                self.logger.info(f"Miner with hotkey {response.axon.hotkey} gave a response object but no patch")
            else:
                uid = next(uid for uid, axon in zip(miner_uids, axons) if axon.hotkey == response.axon.hotkey)
                working_miner_uids.append(uid)
                finished_responses.append(IssueSolution(response.patch))
                process_times.append(response.dendrite.process_time)

        if len(working_miner_uids) == 0:
            self.logger.info("No miners responded. Exiting forward pass...")
            return
        
        # # Add punishment for miners who did not respond
        # bad_miner_uids = [uid for uid in miner_uids if uid not in working_miner_uids]
        # self.update_scores(
        #     np.array([ValidatorDefaults.NO_RESPONSE_MIN] * len(bad_miner_uids)),
        #     bad_miner_uids,
        #     TaskType.LABELLED_ISSUE
        # )

        self.logger.info(f"Running task-specific handlers for {problem.problem_uuid}")

        await self.handle_synthetic_patch_response(
            repo,
            problem,
            finished_responses, 
            process_times,
            working_miner_uids,
            forward_pass_id,
        )

        self.logger.reset_forward_pass_context()

    async def organic_forward(self):
        """
        Organic forward loop.
        """
        # TODO: Should probably check if this specific validator is allowed to
        # send organics
        if not IS_RUNNING_OPEN_ISSUES:
            self.logger.info("Skipping organic forward pass...")
            return

        forward_pass_id = str(uuid.uuid4())
        self.logger.add_forward_pass_context(forward_pass_id)
        self.logger.debug("Starting organic forward pass...")

        # Subsample the highest scoring miners
        miner_uids = [
            uid for uid in range(len(self.metagraph.S))
            if check_uid_availability(self.metagraph, uid, self.config.neuron.vpermit_tao_limit)
        ]
        # Get the top 20% of scores in self.scores
        top_5 = np.argsort(self.scores)[-5:][::-1]
        miner_uids = [uid for uid in miner_uids if uid in top_5]

        self.logger.info(f"Found {len(miner_uids)} miner UIDs: {miner_uids}")

        if len(miner_uids) == 0:
            self.logger.info("No miners available to query. Exiting organic forward pass...")
            return

        axons = [self.metagraph.axons[uid] for uid in miner_uids]

        open_issue: Optional[GitHubOpenIssue] = self.github_handler.select_random_issue()
        if not open_issue:
            self.calculate_organic_rewards()
            return

        current_dir = Path.cwd()
        local_repo_dir = clone_repo(*open_issue.repo.split("/"), current_dir.parent, logger=self.logger)

        self.logger.info("Running test outcomes before...")
        test_outcomes_before: Dict[str, bool] = run_tests(local_repo_dir, self.logger)
        self.logger.info(f"Test outcomes before: {test_outcomes_before}")

        required_tests: List[str] = open_issue.required_tests

        responses: List[CodingTask] = await self.dendrite(
            axons=axons,
            synapse=CodingTask(
                repo=open_issue.repo,
                problem_statement=open_issue.problem_statement,
                patch=None,
            ),
            deserialize=False,
            timeout=timedelta(minutes=self.miner_request_timeout_mins).total_seconds(),
        )

        for r in responses:
            # Only record the submission if there is actually a patch
            if r.patch not in [None, ""]:
                self.logger.info(f"Received responses from miners for organic task", extra=asdict(LogContext(
                    log_type="lifecycle",
                    event_type="miner_submitted_organic",
                    additional_properties={
                        "miner_hotkey": r.axon.hotkey,
                        "patch": r.patch,
                        "response_time": r.dendrite.process_time,
                        "forward_pass_id": forward_pass_id
                    }
                )))
        working_miner_uids = []
        finished_responses = []

        valid_response_indices: List[int] = []
        for i, response in enumerate(responses):
            if not response:
                self.logger.info(f"Miner with hotkey {response.axon.hotkey} did not give a response")
            elif response.patch in [None, ""] or not response.axon or not response.axon.hotkey:
                self.logger.info(f"Miner with hotkey {response.axon.hotkey} gave a response object but no patch")
            else:
                uid = next(uid for uid, axon in zip(miner_uids, axons) if axon.hotkey == response.axon.hotkey)
                # Need to watch out for injections
                patch, test_outcomes_after = preprocess_patch(open_issue.repo, response.patch, IS_RUNNING_OPEN_ISSUES, self.logger)
                if patch != "":
                    working_miner_uids.append(uid)
                    finished_responses.append(IssueSolution(response.patch))

                    self.logger.info(f"Test outcomes for patch of hotkey {response.axon.hotkey}: {test_outcomes_after}")

                    if not self.validate_test_statuses(
                        test_outcomes_before,
                        test_outcomes_after,
                        required_tests
                    ):
                        # error, exit method
                        self.logger.info(f"Response from hotkey {response.axon.hotkey} Failed to make tests pass, not accepting PR ")
                    else:
                        valid_response_indices.append(i)

        if not finished_responses:
            self.calculate_organic_rewards()
            return

        # Lets randomly choose one of the responses to be the correct one
        if not valid_response_indices:
            self.logger.info("No responses pass all required tests. Not submitting PR")

        # TODO: Assign rewards to all who made tests passed
        # TODO: Choose between multiple valid PRs by highest LLM eval score instead of randomly
        correct_idx = random.choice(valid_response_indices)
        correct_patch = finished_responses[correct_idx]
        correct_uid = working_miner_uids[correct_idx]

        # Submit the PR to the repo
        self.github_handler.open_pr(
            correct_patch.patch,
            open_issue,
            PrMetadata(
                agent=self.metagraph.hotkeys[correct_uid],
                issue_num=open_issue.issue_num,
                validator=self.dendrite.keypair.ss58_address,
            ),
        )

        # TODO: What if its an existing PR?
        self.calculate_organic_rewards()

    async def handle_synthetic_patch_response(
        self,
        repo: str,
        problem: GeneratedProblemStatement,
        finished_responses: List[IssueSolution],
        process_times: List[float], 
        working_miner_uids: List[int], 
        forward_pass_id: str,
    ) -> None:
        miner_hotkeys = [self.metagraph.hotkeys[uid] for uid in working_miner_uids]
        try:
            rewards_list = await self.calculate_rewards(
                repo,
                problem,
                finished_responses,
                miner_hotkeys,
                process_times,
                forward_pass_id,
            )
        except Exception as e:
            self.logger.exception(f"Error calculating rewards: {e}")
            return

        for hk, reward in zip(miner_hotkeys, rewards_list):
            self.logger.info(f"Reward for miner {hk} is {reward}", extra=asdict(LogContext(
                log_type="lifecycle",
                event_type="reward_calculated",
                additional_properties={
                    "miner_hotkey": hk, 
                    "reward": reward,
                    "forward_pass_id": forward_pass_id,
                }
            )))

        # reward the miners who succeeded
        self.update_scores(
            rewards_list,
            working_miner_uids,
            TaskType.LABELLED_ISSUE
        )

        try:
            await self.upload_solution(
                problem.problem_statement,
                finished_responses,
                rewards_list.tolist(),
                miner_hotkeys,
            )
        except Exception as e:
            self.logger.exception(f"Error uploading solution: {e}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model",
        choices=SUPPORTED_VALIDATOR_MODELS,
        default=ValidatorDefaults.MODEL,
        help="Model to use for problem generation and eval. Currently, only OpenAI models are supported."
    )
    parser.add_argument(
        "--miner-request-timeout",
        type=int,
        default=ValidatorDefaults.CODINGTASK_TIMEOUT_MINS,
        help="How long to wait for a response from the miners, in minutes",
    )
    parser.add_argument(
        "--use-mock-responses",
        action="store_true",
        default=False,
        help="Run validator in mock mode, assigning dummy scores"
    )
    args, _ = parser.parse_known_args()
    return args

# The main function parses the configuration and runs the validator.
if __name__ == "__main__":
    with Validator(**vars(parse_args())) as validator:
        while True:
            time.sleep(5)
