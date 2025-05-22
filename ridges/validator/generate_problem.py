import os
from dataclasses import asdict, dataclass
from pathlib import Path
from textwrap import dedent
from typing import List, Final, Union, Callable
import random
import uuid

import openai
from jinja2 import Template
from pydantic import BaseModel

from agentao.helpers.classes import FilePair, GeneratedProblemStatement, \
    ValidatorModelStats, IngestionHeuristics
from agentao.helpers.clients import LogContext
from agentao.helpers.helpers import calculate_price
from agentao.validator.ingest import get_all_filepairs

from logging import Logger

PROBLEM_STATEMENT_TEMPLATE: Final[Template] = Template(
    dedent("""
    You are a skilled software engineering assistant. You will be provided with multiple files as context. Each file will contain portions of code, documentation, or relevant information about a software system. Your task is to come up with a specific software engineering problem that requires a solution to involve at least two of these files. You will generate a list of these problems, in the generated_problems array response.

    Further, once you have a problem statement, generate a checklist of points to consider and things that should be present in the solution (for example, are the correct Github API calls made if its a function that interfaces with the api). Generate several of these into dynamic_checklist field.
    Some additional guidelines are:
    - Do not output anything other than software engineering problem
    - The problem description should be very detailed and meticulous. It should contain sufficient context such that someone equipped with the codebase and your problem statement will have enough information to implement
    - The problem should be solvable by an autonomous SWE which can do things like installing PyPi packages, but cannot do things like make cloud provider accounts and register for other services manually.
    - The problem should not be overly difficult to implement, and should be fairly easy and not take too many LLM calls. 
    - Do not disclose which files would need to be modified to solve the problem.

    Here are the files:
    {% for file in files %}
    Filename: {{ file.path }}
    ```python3
    {{ file.contents }}
    ```
    {% endfor %}
    ```
    """)
)

# TODO: Add support for other model providers
OPENAI_CLIENT: Final[openai.Client] = openai.Client(api_key=os.getenv("OPENAI_API_KEY"))

class GeneratedProblem(BaseModel):
    problem_statement: str
    dynamic_checklist: List[str]

@dataclass
class ProblemGeneratorParameters:
    filepair_selection_logic: Callable[[List[FilePair]], FilePair]
    prompt_template: Template
    num_problems_to_gen: int
    problem_gen_model: str


# We use pydantic for some classes because OpenAI json output can structure based on that
class ListOfGeneratedProblems(BaseModel):
    generated_problem_statements: List[GeneratedProblem]


def highest_cosine_filepair_selector(file_pairs: List[FilePair]) -> FilePair:
    selected_file_pair = sorted(
        file_pairs,
        key=lambda x: float(x.cosine_similarity),
        reverse=True
    )[random.randint(1, 10)]

    return selected_file_pair

def create_problem_statements(
    validator_llm: str,
    repo: str,
    local_repo_dir: Path,
    problems: Union[int, List[str]],
    ingestion_heuristics: IngestionHeuristics,
    logger: Logger
) -> List[GeneratedProblemStatement]:
    if isinstance(problems, int):
        problem_generator_params = ProblemGeneratorParameters(
            filepair_selection_logic=highest_cosine_filepair_selector,
            prompt_template=PROBLEM_STATEMENT_TEMPLATE,
            num_problems_to_gen=problems,
            problem_gen_model=validator_llm,
        )

        problem_statements: List[GeneratedProblemStatement] = generate_problems_for_single_repo(
            repo_path=local_repo_dir,
            ingestion_heuristics=ingestion_heuristics,
            problem_generation_params=problem_generator_params,
            logger=logger
        )
    else:
        raise ValueError(
            f"config[{repo}]['problems'] must be a list of strings or an integer. "
            f"Current value of `{problems}` is invalid"
        )
    return problem_statements


def generate_problems_for_single_repo(
    repo_path: Path,
    ingestion_heuristics: IngestionHeuristics,
    problem_generation_params: ProblemGeneratorParameters,
    logger: Logger
) -> List[GeneratedProblemStatement]:
    file_pairs = get_all_filepairs(
        repo_path,
        logger=logger,
        heuristics=ingestion_heuristics,
        refresh=False,
    )

    # Generate one problem statement, with prompt and model to benchmark
    problem_statements_list = generate_problem_statements(
        filepairs=file_pairs,
        parameters=problem_generation_params,
        logger=logger
    )
    return problem_statements_list



def generate_problem_statements(
    filepairs: List[FilePair],
    parameters: ProblemGeneratorParameters,
    logger: Logger
) -> List[GeneratedProblemStatement]:
    selected_file_pair = parameters.filepair_selection_logic(filepairs)
    prompt_text = parameters.prompt_template.render(
        dict(
            files=selected_file_pair.files
        )
    )

    model_map = {
        "gpt4omini": "gpt-4o-mini",
        "gpt4o": "gpt-4o"
    }
    model = parameters.problem_gen_model if parameters.problem_gen_model not in model_map \
        else model_map[parameters.problem_gen_model]


    completion = OPENAI_CLIENT.beta.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": prompt_text},
            {"role": "user", "content": f"Generate the list of problem statements. Generate exactly {parameters.num_problems_to_gen} statements, no more and no less"},
        ],
        response_format=ListOfGeneratedProblems,
    )

    parsed_response = completion.choices[0].message.parsed.generated_problem_statements
    prompt_tokens, completion_tokens = completion.usage.prompt_tokens, completion.usage.completion_tokens
    cost = calculate_price(model, prompt_tokens, completion_tokens)

    logger.info(f"Generated problem statement for {cost} with {model}", extra=asdict(LogContext(
            log_type="lifecycle",
            event_type="openai_cost",
        )))

    # Generate a UUID for each problem statement
    problem_uuid = str(uuid.uuid4())

    return [
        GeneratedProblemStatement(
            problem_uuid=problem_uuid,
            prompt=prompt_text,
            model=model,
            problem_statement=statement.problem_statement,
            dynamic_checklist=statement.dynamic_checklist,
            model_stats=ValidatorModelStats(
                input_tokens=prompt_tokens,
                output_tokens=completion_tokens,
                cost=cost,
            ),
            context_files=[file.contents for file in selected_file_pair.files]
        ) for statement in parsed_response
    ]
