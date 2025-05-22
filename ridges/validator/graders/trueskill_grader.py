from typing import List, Dict
import trueskill
import numpy as np
from logging import Logger
import os
import json

from agentao.validator.graders.abstract_grader import GraderInterface, MinerSubmission
from agentao.validator.graders.float_grader import FloatGrader, MockFloatGrader


from dataclasses import asdict
from agentao.helpers.clients import LogContext

class TrueSkillGrader(GraderInterface):
    """
    A grader that uses the TrueSkill rating system to grade miners. The 
    ratings are updated based on the performance of the miners in the
    forward loop, and then normalized with a logistic function.
    """
    def __init__(self, logger: Logger):
        self.logger = logger
        self.env = trueskill.TrueSkill()
        self.ratings: Dict[str, trueskill.Rating] = {}
        self.float_grader = FloatGrader(logger)
        self.num_runs = 0
        self.apha = np.log(4) / self.env.beta

        # Initialize cached ratings
        self.initialize()

    def initialize(self) -> None:
        """
        Initialize ratings for miners if available.
        """
        try:
            # Get the parent directory of this file
            parent_dir = os.path.dirname(os.path.abspath(__file__))
            with open(parent_dir + "/ratings.json", "r") as f:
                state = json.load(f)
        except FileNotFoundError as e:
            # The file did not exist, so we do nothing
            return
        for miner_hotkey, rating in state.items():
            self.ratings[miner_hotkey] = self.env.create_rating(mu=rating[0], sigma=rating[1])

        self.logger.info(f"Loaded Trueskill ratings from file")

    def save_state(self) -> None:
        """
        Save the state of the ratings to a file.
        """
        # Get the parent directory of this file
        parent_dir = os.path.dirname(os.path.abspath(__file__))
        with open(parent_dir + "/ratings.json", "w") as f:
            json.dump({k: [v.mu, v.sigma] for k, v in self.ratings.items()}, f)

    def grade(self, submissions: List[MinerSubmission], forward_pass_id: str) -> List[float]:
        # Initialize any new miners
        for submission in submissions:
            if submission.miner_hotkey not in self.ratings:
                self.ratings[submission.miner_hotkey] = self.env.create_rating()

        # Run float scores
        float_scores = self.float_grader.grade(submissions, forward_pass_id)
        for index, submission in enumerate(submissions):
            float_grade_assigned = float_scores[index]

            self.logger.info(f"Graded miner {submission.miner_hotkey} with score of {float_grade_assigned} for question {submission.problem.problem_uuid}", extra=asdict(LogContext(
                log_type="lifecycle",
                event_type="solution_selected",
                additional_properties={
                    "question_id": submission.problem.problem_uuid,
                    "grade": float_grade_assigned,
                    "miner_hotkey": submission.miner_hotkey,
                    "forward_pass_id": forward_pass_id,
                }
            )))

        # We run the rating system thrice for steadier results when we first
        # initialize the ratings
        if len(submissions) > 1:
            num_runs = 1 if self.num_runs > 5 else 3
            for _ in range(num_runs):
                self.update_ratings(submissions, float_scores)

            self.num_runs += 1

        # Calculate normalized ratings
        ratings = []
        mean_score = np.mean([r.mu - 3*r.sigma for r in self.ratings.values()])
        for index, submission in enumerate(submissions):
            if float_scores[index] == 0.0:
                ratings.append(0.0)
                continue
            miner_rating = self.ratings[submission.miner_hotkey]
            miner_rating = miner_rating.mu - 3 * miner_rating.sigma
            miner_rating = 1 / (1 + np.exp(-self.apha * (miner_rating - mean_score)))
            ratings.append(miner_rating)

            self.logger.info(f"Graded miner {submission.miner_hotkey} with score of {miner_rating}", extra=asdict(LogContext(
                log_type="lifecycle",
                event_type="trueskill_score",
                additional_properties={
                    "question_id": submission.problem.problem_uuid,
                    "grade": miner_rating,
                    "miner_hotkey": submission.miner_hotkey,
                    "forward_pass_id": forward_pass_id,
                }
            )))

        self.save_state()

        return ratings

    def update_ratings(
            self, 
            submissions: List[MinerSubmission], 
            float_scores: List[float]
    ) -> None:
        """
        Update the ratings of the miners  based on their performance.
        """
        raw_scores = {}
        for fs, submission in zip(float_scores, submissions):
            raw_scores[submission.miner_hotkey] = fs

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


class MockTrueSkillGrader(TrueSkillGrader):
    def __init__(self, logger: Logger):
        super().__init__(logger)
        self.float_grader = MockFloatGrader(logger)
