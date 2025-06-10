import asyncio
import json
import os
from typing import TYPE_CHECKING, List, Dict
import trueskill
import numpy as np

from shared.logging_utils import get_logger
from validator.challenge.base import BaseResponse
from validator.dependancies import get_results_dir
from validator.evaluation.graders.abstract_grader import GraderInterface
from validator.evaluation.graders.float_grader import FloatGrader
from validator.evaluation.log_score import log_score
if TYPE_CHECKING:
    from validator.challenge.codegen.challenge import CodegenChallenge

logger = get_logger(__name__)

class TrueSkillGrader(GraderInterface):
    """
    A grader that uses the TrueSkill rating system to grade miners. The 
    ratings are updated based on the performance of the miners in the
    forward loop, and then normalized with a logistic function.
    """
    def __init__(self, problem: 'CodegenChallenge'):
        self.env = trueskill.TrueSkill()
        self.ratings: Dict[str, trueskill.Rating] = {}
        self.float_grader = FloatGrader(problem)
        self.num_runs = 0
        self.apha = np.log(4) / self.env.beta
        self.problem = problem

        # Initialize cached ratings
        self.initialize()

    def initialize(self) -> None:
        """
        Initialize ratings for miners if available.
        """
        try:
            with open(get_results_dir() / "trueskill_ratings.json", "r") as f:
                state = json.load(f)
        except FileNotFoundError as e:
            # The file did not exist, so we do nothing
            return
        for miner_hotkey, rating in state.items():
            self.ratings[miner_hotkey] = self.env.create_rating(mu=rating[0], sigma=rating[1])

        logger.info(f"Loaded Trueskill ratings from file")

    def save_state(self) -> None:
        """
        Save the state of the ratings to a file.
        """
        with open(get_results_dir() / "trueskill_ratings.json", "w") as f:
            json.dump({k: [v.mu, v.sigma] for k, v in self.ratings.items()}, f)

    async def grade(self, responses: List[BaseResponse]) -> List[float]:
        # Run float scores
        float_scores_by_hotkey = await self.float_grader.grade(responses)

        # Initialize any new miners
        for response in responses:
            if response.miner_hotkey not in self.ratings:
                self.ratings[response.miner_hotkey] = self.env.create_rating()
            logger.info(f"Graded miner {response.miner_hotkey} with score of {float_scores_by_hotkey[response.miner_hotkey]} for question {response.response_id}")

        # We run the rating system thrice for steadier results when we first
        # initialize the ratings
        if len(responses) > 1:
            num_runs = 1 if self.num_runs > 5 else 3
            for _ in range(num_runs):
                self.update_ratings(responses, [float_scores_by_hotkey[response.miner_hotkey] for response in responses])

            self.num_runs += 1

        # Calculate normalized ratings
        log_tasks = []
        ratings = {}
        mean_score = np.mean([r.mu - 3*r.sigma for r in self.ratings.values()])
        for response in responses:
            if float_scores_by_hotkey[response.miner_hotkey] == 0.0:
                ratings[response.miner_hotkey] = 0.0
                log_tasks.append(log_score("trueskill", self.problem.validator_hotkey, response.miner_hotkey, 0.0))
                continue
            miner_rating = self.ratings[response.miner_hotkey]
            miner_rating = miner_rating.mu - 3 * miner_rating.sigma
            miner_rating = 1 / (1 + np.exp(-self.apha * (miner_rating - mean_score)))
            ratings[response.miner_hotkey] = miner_rating
            log_tasks.append(log_score("trueskill", self.problem.validator_hotkey, response.miner_hotkey, miner_rating))

            logger.info(f"Graded miner {response.miner_hotkey} with score of {miner_rating}")

        if log_tasks:
            await asyncio.gather(*log_tasks)

        self.save_state()
        return ratings

    def update_ratings(
            self, 
            responses: List[BaseResponse], 
            float_scores: List[float]
    ) -> None:
        """
        Update the ratings of the miners  based on their performance.
        """
        raw_scores = {}
        for fs, response in zip(float_scores, responses):
            raw_scores[response.miner_hotkey] = fs

        sorted_scores = sorted(raw_scores.items(), key=lambda x: x[1], reverse=True)

        ratings_groups = []
        for k, v in self.ratings.items():
            if k in raw_scores:
                ratings_groups.append({k: v})

        ranks = []
        for x in ratings_groups:
            for mhk, _ in x.items():
                for i, (mhk2, _) in enumerate(sorted_scores):
                    if mhk == mhk2:
                        ranks.append(i)
                        break

        new_ratings = self.env.rate(ratings_groups, ranks=ranks)

        # Save new ratings
        for rating_result in new_ratings:
            for mhk, rating in rating_result.items():
                self.ratings[mhk] = rating
