# TODO ADAM: There is a lot of redundant data in problem_statistics.json. We
#            should rather make this file load the data from the source, which
#            is the /evaluator/datasets directory.

import json
import pathlib

from enum import Enum
from typing import Tuple, Optional



class ProblemStatisticsProblemSuite(str, Enum):
    swebench_verified = "swebench_verified"
    polyglot_py = "polyglot_py"
    polyglot_js = "polyglot_js"

class ProblemStatisticsProblemDifficulty(str, Enum):
    easy = "easy"
    medium = "medium"
    hard = "hard"
    impossible = "impossible"



with open(pathlib.Path(__file__).parent / "problem_statistics.json", "r") as f:
    problem_statistics_json = json.load(f)



def get_problem_statistics_by_problem_name(problem_name: str) -> Tuple[ProblemStatisticsProblemSuite, Optional[ProblemStatisticsProblemDifficulty]]:
    if not problem_name in problem_statistics_json:
        return None

    problem_statistics = problem_statistics_json[problem_name]
    return problem_statistics["problem_suite"], problem_statistics["problem_difficulty"]