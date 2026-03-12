from models.problem import ProblemDifficulty
from typing import Optional

def _swebench_verified_difficulty_to_problem_difficulty(difficulty: str) -> Optional[ProblemDifficulty]:
    if difficulty == "<15 min fix":
        return ProblemDifficulty.EASY
    elif difficulty == "15 min - 1 hour":
        return ProblemDifficulty.MEDIUM
    elif difficulty == "1-4 hours":
        return ProblemDifficulty.HARD
    elif difficulty == ">4 hours":
        return ProblemDifficulty.IMPOSSIBLE
    else:
        return None