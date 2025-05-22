import os
import random
from dataclasses import dataclass
from itertools import combinations
from textwrap import dedent
from typing import List
from typing import Tuple, Dict, Final

import openai
from pydantic import BaseModel

from agentao.helpers.classes import GeneratedProblemStatement
from agentao.validator.graders.abstract_grader import GraderInterface, MinerSubmission

from logging import Logger

NUM_ELO_ROUNDS: Final[int] = 2

class EloGrader(GraderInterface):
    def __init__(self, logger: Logger):
        self.logger = logger

    def grade(self, submissions: List[MinerSubmission], _forward_pass_id: str) -> List[float]:
        scores = rank_elo(submissions, self.logger)
        return scores


class EloRating:
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

    def update_ratings(self, player_a, player_b, score_a):
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


class WinLoss(BaseModel):
    model_1_victor: bool
    model_2_victor: bool
    is_draw: bool
    explanation: str

@dataclass
class SolvedProblem:
    problem: GeneratedProblemStatement
    solving_model: str
    cost_per_output_token: str
    time_to_solve_secs: int
    solution: str


def generate_matches(indices: List[str]) -> List[Tuple[str, str]]:
    """Run a tournament comparing all solutions multiple times."""
    matches: List[Tuple[str, str]] = []
    solution_pairs: List[Tuple[str, str]] = list(combinations(indices, 2))

    # Run multiple rounds
    for _ in range(NUM_ELO_ROUNDS):
        random.shuffle(solution_pairs)  # Randomize match order
        for sol_a, sol_b in solution_pairs:
            matches.append((sol_a, sol_b))

    return matches


def generate_win_loss_for_problem(
    local_elo: EloRating,
    problem: GeneratedProblemStatement,
    solution_0_and_index_str: Tuple[MinerSubmission, str],
    solution_1_and_index_str: Tuple[MinerSubmission, str],
    openai_client: openai.Client,
) -> None:
    solution_0, solution_0_index_str = solution_0_and_index_str
    solution_1, solution_1_index_str = solution_1_and_index_str
    prompt = dedent(f"""
    You are an unbiased code evaluator, who takes in a problem statement, plus a checklist of factors that a solution to the statement should consider.
    For context, you will also be given the files used to generate a solution.
    Then, you will be given two solutions, Determine which solution is better.
    If they are equal in quality based on factors like how logical they are, cleanliness of code, as well as the factors included in the checklist, return is_draw = True and victor_model = None
    Otherwise, return is_draw = False and victor_model = the model id of the better solution.
    There is one ground truth solution, though it may not be one the solutions provided. The goal is to evenutally find this coherent solution (that works and was merged). The winner should generally reflect which model is more likely to be this ground truth real world winner.
    ------
    {problem.to_detailed_format()}
    ------
    """)

    context = dedent(f"""
    Model 1 solution: {solution_0.solution}
    Model 2 solution: {solution_1.solution}
    """)

    completion = openai_client.beta.chat.completions.parse(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": context},
        ],
        response_format=WinLoss,
    )

    output = completion.choices[0].message.parsed

    outputs = [output.model_1_victor, output.model_2_victor, output.is_draw]

    if sum(outputs) < 1:
        raise ValueError(
            f"Invalid output: {output}. None of (1, 2, draw) is true. Received: {outputs}"
        )

    if sum(outputs) > 1:
        raise ValueError(
            f"Invalid output: {output}. More than 1 value is true from (1, 2, draw). Received: {outputs}"
        )

    if output.is_draw:
        local_elo.update_ratings(solution_0_index_str, solution_1_index_str, 0.5)

    if output.model_1_victor:
        local_elo.update_ratings(solution_0_index_str, solution_1_index_str, 1.0)
    else:
        local_elo.update_ratings(solution_1_index_str, solution_0_index_str, 1.0)


def get_raw_elo_rankings(elox: EloRating, indices: List[str]) -> Dict[str, float]:
    """Get current rankings of all solutions."""
    rankings = {name: elox.get_rating(name) for name in indices}
    return dict(sorted(rankings.items(), key=lambda x: x[1], reverse=True))


def rank_elo(submissions: List[MinerSubmission], logger: Logger) -> List[float]:
    openai_client: Final[openai.Client] = openai.Client(api_key=os.getenv("OPENAI_API_KEY"))

    local_elo = EloRating()
    problem: GeneratedProblemStatement = submissions[0].problem
    str_indices: List[str] = [str(i) for i in range(len(submissions))]

    for first, second in generate_matches(str_indices):
        solution_0: MinerSubmission = submissions[int(first)]
        solution_1: MinerSubmission = submissions[int(second)]

        generate_win_loss_for_problem(
            local_elo,
            problem,
            solution_0_and_index_str=(solution_0, first),
            solution_1_and_index_str=(solution_1, second),
            openai_client=openai_client,
        )
        logger.info(f"Current rankings: {get_raw_elo_rankings(local_elo, str_indices)}")

    raw_elo_model_rankings = get_raw_elo_rankings(local_elo, str_indices)
    logger.info(f"Raw elo model rankings: {raw_elo_model_rankings}")

    scores = [raw_elo_model_rankings[str(i)] for i in range(len(submissions))]
    return scores
