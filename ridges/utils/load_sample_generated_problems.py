import json
from typing import List, Dict

from agentao.helpers.classes import dict_to_dataclass_or_basemodel, GeneratedProblemStatement
from agentao.helpers.constants import SAMPLE_GENERATED_PROBLEMS_FILE


def load_sample_problems() -> List[GeneratedProblemStatement]:
    with open(SAMPLE_GENERATED_PROBLEMS_FILE, 'r') as file:
        generated_problems_obj: List[Dict] = json.load(file)

    if not isinstance(generated_problems_obj, list):
        raise ValueError("JSON file does not contain a list.")

    generated_problems: List[GeneratedProblemStatement] = [
        dict_to_dataclass_or_basemodel(GeneratedProblemStatement, problem_obj)
        for problem_obj in generated_problems_obj
    ]
    return generated_problems
