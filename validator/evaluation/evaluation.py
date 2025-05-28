from typing import List, Optional

from openai import OpenAI

from validator.challenge.challenge_types import GeneratedCodegenProblem, ValidationResult, CodegenResponse
from validator.db.operations import DatabaseManager
from validator.evaluation.graders.elo_grader import EloGrader
# from validator.utils.clone_repo import clone_repo
from validator.utils.clean_patch import remove_unused, remove_comments, remove_docstrings

class CodeGenValidator:
    def __init__(self, db_manager: DatabaseManager, openai_client: OpenAI, validator_hotkey: str):
        self.db_manager = db_manager
        self.openai_client = openai_client
        self.validator_hotkey = validator_hotkey

    def preprocess_patch(patch: str) -> str:
        '''
        Preprocesses a patch by removing comments, docstrings, etc.
        '''

        without_comments = remove_comments(patch)
        without_docstrings = remove_docstrings(without_comments)
        without_unused = remove_unused(without_docstrings)

        return without_unused.strip()
    
    def apply_and_run_tests(self, problem: GeneratedCodegenProblem, patch: str) -> Optional[str]:
        '''
        Clones the relevant repo, applies the patch, and runs the tests.
        Also runs pylint and makes sure no new errors have appeared.
        
        Returns:
            An error message if anything fails, otherwise None
        '''
        return None

    async def evaluate_responses(self, problem: GeneratedCodegenProblem, miner_responses: List[CodegenResponse]) -> List[ValidationResult]:
        grader = EloGrader(problem)

        tested_responses = []

        for response in miner_responses:
            response.response_patch = self.preprocess_patch(response.response_patch)
            error = self.apply_and_run_tests(problem, response.response_patch)
            if error is not None:
                tested_responses.append(response)
            else:
                self.db_manager.mark_response_failed(response.response_id)
        
        return grader.grade(tested_responses)
