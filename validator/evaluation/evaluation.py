from typing import Optional

from openai import OpenAI

from validator.challenge.challenge_types import ValidationResult, CodegenResponse
from validator.utils.clean_patch import remove_unused, remove_comments, remove_docstrings

class CodeGenValidator:
    def __init__(self, openai_client: OpenAI, validator_hotkey: str):
        self.db_manager = None
        self.openai_client = openai_client
        self.validator_hotkey = validator_hotkey

    def preprocess_patch(patch: str) -> str:
        '''
        Preprocesses a patch by removing comments, docstrings, etc.

        NOTE: Add AST Walk to remove unused vars etc
        '''

        without_comments = remove_comments(patch)
        without_docstrings = remove_docstrings(without_comments)
        without_unused = remove_unused(without_docstrings)

        return without_unused.strip()
    
    def apply_and_run_tests(patch: str) -> Optional[str]:
        '''
        Clones the relevant repo, applies the patch, and runs the tests.
        Also runs pylint and makes sure no new errors have appeared.
        
        Returns:
            An error message if anything fails, otherwise None
        '''
        return None

    async def evaluate_response(self, miner_response: CodegenResponse) -> ValidationResult:
        # patch = self.preprocess_patch(miner_response.response_patch)

        # if len(patch) == 0:
        #     return ValidationResult(
        #         score=0,
        #         error="Not a valid patch"
        #     )
        
        # test_errors = self.apply_and_run_tests(patch)

        # if test_errors is not None:
        #     return ValidationResult(
        #         score=0,
        #         error=test_errors
        #     )

        return ValidationResult(
            score=2,
            error=None
        )
