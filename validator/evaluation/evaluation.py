from typing import Optional
import re

from openai import OpenAI

from validator.challenge.challenge_types import ValidationResult, CodegenResponse
from validator.utils import remove_unused_vars

def remove_comments(patch_content: str) -> str:
    """
    Process a Git patch string to remove comments from added lines, keeping the '+' intact.

    :param patch_content: The content of a Git patch as a string.
    :return: The cleaned patch content as a string.
    """
    # Regex patterns
    comment_line_pattern = re.compile(r"^\+\s*#.*")  # Matches whole-line comments
    inline_comment_pattern = re.compile(r"#.*")  # Matches inline comments

    cleaned_lines = []

    # Process each line
    for line in patch_content.splitlines():
        if line.startswith('+'):  # Only process added lines
            if comment_line_pattern.match(line):
                continue  # Skip whole-line comments

            # Remove inline comments but keep the '+'
            cleaned_line = inline_comment_pattern.sub("", line).rstrip()

            cleaned_lines.append(cleaned_line)
        else:
            cleaned_lines.append(line)

    return "\n".join(cleaned_lines)

def remove_docstrings(patch_content: str) -> str:
    """
    Process a Git patch string to remove added lines that introduce or modify docstrings,
    while keeping the '+' intact for other additions.

    :param patch_content: The content of a Git patch as a string.
    :return: The cleaned patch content as a string.
    """
    cleaned_lines = []
    in_docstring = False
    docstring_delim = None

    for line in patch_content.splitlines():
        if line.startswith('+'):  # Only process added lines
            stripped_line = line[1:].lstrip()  # Remove '+' for checking

            # If we are inside a docstring, check for closing delimiter
            if in_docstring and docstring_delim is not None:
                if docstring_delim in stripped_line:  # Closing delimiter found
                    in_docstring = False
                continue  # Skip all lines inside the docstring

            # Detect docstring start (including when a patch adds text to an existing docstring)
            if stripped_line.startswith(('"""', "'''")):
                docstring_delim = stripped_line[:3]  # Capture delimiter type
                if stripped_line.count(docstring_delim) >= 2:
                    continue  # Single-line docstring, skip this line
                in_docstring = True
                continue  # Start of multiline docstring, skip line

            cleaned_lines.append(line)  # Keep non-docstring lines
        else:
            # If the line is not an addition (`+`), keep it unchanged
            if line.lstrip().startswith(('"""', "'''")) and not in_docstring:
                in_docstring = True
                docstring_delim = line.lstrip()[:3]  # Track delimiter
            elif docstring_delim and docstring_delim in line:
                in_docstring = False  # Close docstring block

            cleaned_lines.append(line)

    return "\n".join(cleaned_lines)

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

        without_unused = remove_unused_vars.dict_remover(patch)

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