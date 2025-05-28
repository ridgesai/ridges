from itertools import combinations
from logging import Logger
from fiber.logging_utils import get_logger
import os
import random
from textwrap import dedent
from typing import Dict, Final, List, Optional, Tuple, TypeVar

import openai
from pydantic import BaseModel

from validator.challenge.challenge_types import (
    CodegenResponse,
    GeneratedCodegenProblem,
)
from .abstract_grader import GraderInterface


class WinLoss(BaseModel):
    model_1_victor: bool
    model_2_victor: bool
    is_draw: bool
    explanation: str


class EloArena:
    def __init__(self, k_factor=32, default_rating=1200):
        """
        Initialize the Elo rating system.

        Args:
            k_factor (int): The maximum rating change possible in one match
            default_rating (int): The default rating for new players
        """
        self.k_factor = k_factor
        self.default_rating = default_rating
        self.players = {}

    def get_expected_score(self, rating_a, rating_b):
        """
        Calculate the expected score for player A against player B.

        Args:
            rating_a (float): Rating of player A
            rating_b (float): Rating of player B

        Returns:
            float: Expected score between 0 and 1
        """
        return 1 / (1 + 10 ** ((rating_b - rating_a) / 400))

    def update_ratings(self, player_a, player_b, score_a: float):
        """
        Update ratings for two players based on their game outcome.

        Args:
            player_a (str): Identifier for player A
            player_b (str): Identifier for player B
            score_a (float): Actual score for player A (1 for win, 0.5 for draw, 0 for loss)

        Returns:
            tuple: New ratings for player A and player B
        """
        # Get current ratings or assign default
        rating_a = self.players.get(player_a, self.default_rating)
        rating_b = self.players.get(player_b, self.default_rating)

        # Calculate expected scores
        expected_a = self.get_expected_score(rating_a, rating_b)

        # Calculate rating changes
        change_a = self.k_factor * (score_a - expected_a)

        # Update ratings
        new_rating_a = rating_a + change_a
        new_rating_b = rating_b - change_a

        # Store new ratings
        self.players[player_a] = new_rating_a
        self.players[player_b] = new_rating_b

        return new_rating_a, new_rating_b

    def get_rating(self, player):
        """
        Get the current rating for a player.

        Args:
            player (str): Player identifier

        Returns:
            float: Current rating of the player
        """
        return self.players.get(player, self.default_rating)

    def raw_rankings(self) -> Dict[str, float]:
        return dict(sorted(self.players.items(), key=lambda x: x[1], reverse=True))

    def get_scores(self) -> Dict[str, float]:
        """Get a score between 0 and 1 for each player"""
        if len(self.players) == 0:
            return {}

        if len(self.players) == 1:
            return {list(self.players.keys())[0]: 1.0}

        max_rating = max(self.players.values())
        min_rating = min(self.players.values())
        return {
            player: (rating - min_rating) / (max_rating - min_rating)
            for player, rating in self.players.items()
        }


class EloGrader(GraderInterface):
    def __init__(self, problem: GeneratedCodegenProblem):
        self.logger = get_logger(__name__)
        self.problem = problem

    def grade(self, responses: List[CodegenResponse]) -> Dict[str, float]:
        """
        Grade a list of responses using the Elo rating system and return a dictionary of scores for each response by hotkey
        """
        arena = self.rank_elo(responses)
        return arena.get_scores()

    def get_prompt(self) -> str:
        return dedent(
            f"""
        You are an unbiased code evaluator, who takes in a problem statement, plus a checklist of factors that a solution to the statement should consider.
        For context, you will also be given the files used to generate a solution.
        Then, you will be given two solutions, Determine which solution is better.
        If they are equal in quality based on factors like how logical they are, cleanliness of code, as well as the factors included in the checklist, return is_draw = True and victor_model = None
        Otherwise, return is_draw = False and victor_model = the model id of the better solution.
        There is one ground truth solution, though it may not be one the solutions provided. The goal is to evenutally find this coherent solution (that works and was merged). The winner should generally reflect which model is more likely to be this ground truth real world winner.
        ------
        {self.problem.to_detailed_format()}
        ------
        """
        )

    def get_context(
        self, response_1: CodegenResponse, response_2: CodegenResponse
    ) -> str:
        return dedent(
            f"""
        Model 1 solution: {response_1.response_patch}
        Model 2 solution: {response_2.response_patch}
        """
        )

    def get_completion(self, prompt: str, context: str) -> WinLoss:
        """
        Get a completion from the OpenAI API. Override this method to use a different model or API.
        """
        completion = openai.Client(
            api_key=os.getenv("OPENAI_API_KEY")
        ).beta.chat.completions.parse(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": context},
            ],
            response_format=WinLoss,
        )
        return completion.choices[0].message.parsed

    def compare_responses(
        self, response_1: CodegenResponse, response_2: CodegenResponse
    ) -> float:
        prompt = self.get_prompt()

        context = self.get_context(response_1, response_2)

        completion = self.get_completion(prompt, context)

        outputs = [
            completion.model_1_victor,
            completion.model_2_victor,
            completion.is_draw,
        ]

        if sum(outputs) < 1:
            raise ValueError(
                f"Invalid completion: {completion}. None of (1, 2, draw) is true. Received: {outputs}"
            )

        if sum(outputs) > 1:
            raise ValueError(
                f"Invalid completion: {completion}. More than 1 value is true from (1, 2, draw). Received: {outputs}"
            )

        if completion.model_1_victor:
            return 1.0
        elif completion.model_2_victor:
            return 0.0
        else:
            return 0.5

    def rank_elo(self, responses: List[CodegenResponse]) -> EloArena:
        arena = EloArena()

        for response_1, response_2 in generate_matches(responses):
            comparison = self.compare_responses(response_1, response_2)
            arena.update_ratings(
                response_1.miner_hotkey, response_2.miner_hotkey, comparison
            )
            self.logger.info(f"Current rankings: {arena.raw_rankings()}")

        self.logger.info(f"Raw Elo model rankings: {arena.raw_rankings()}")

        return arena


NUM_ELO_ROUNDS: Final[int] = 2

T = TypeVar("T")


def generate_matches(responses: List[T]) -> List[Tuple[T, T]]:
    """Run a tournament comparing all solutions multiple times."""
    matches: List[Tuple[T, T]] = []
    pairs: List[Tuple[T, T]] = list(combinations(responses, 2))

    for _ in range(NUM_ELO_ROUNDS):
        random.shuffle(pairs)
        for pair in pairs:
            matches.append(pair)

    return matches
