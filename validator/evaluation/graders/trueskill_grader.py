from typing import TYPE_CHECKING, List, Dict
import trueskill
import numpy as np

from shared.logging_utils import get_logger
from validator.challenge.base import BaseResponse
from validator.evaluation.graders.abstract_grader import GraderInterface
from validator.evaluation.graders.float_grader import FloatGrader
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

    def grade(self, responses: List[BaseResponse]) -> List[float]:
        # Initialize any new miners
        for response in responses:
            if response.miner_hotkey not in self.ratings:
                self.ratings[response.miner_hotkey] = self.env.create_rating()

        # Run float scores
        float_scores = self.float_grader.grade(responses)
        for index, response in enumerate(responses):
            float_grade_assigned = float_scores[index]

            print(f"Graded miner {response.miner_hotkey} with score of {float_grade_assigned} for question {response.response_id}")

        # We run the rating system thrice for steadier results when we first
        # initialize the ratings
        if len(responses) > 1:
            num_runs = 1 if self.num_runs > 5 else 3
            for _ in range(num_runs):
                self.update_ratings(responses, float_scores)

            self.num_runs += 1

        # Calculate normalized ratings
        ratings = []
        mean_score = np.mean([r.mu - 3*r.sigma for r in self.ratings.values()])
        for index, response in enumerate(responses):
            if float_scores[index] == 0.0:
                ratings.append(0.0)
                continue
            miner_rating = self.ratings[response.miner_hotkey]
            miner_rating = miner_rating.mu - 3 * miner_rating.sigma
            miner_rating = 1 / (1 + np.exp(-self.apha * (miner_rating - mean_score)))
            ratings.append(miner_rating)

            logger.info(f"Graded miner {response.miner_hotkey} with score of {miner_rating}")

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
