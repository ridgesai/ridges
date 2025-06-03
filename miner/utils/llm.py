from typing import List
from openai import OpenAI

from validator.challenge.challenge_types import File

def generate_solution_with_openai(problem_statement: str, relevant_files: List[File], api_key: str) -> str:
    """
    Uses OpenAI to generate a code solution for the given problem statement.
    The LLM is instructed to return only a valid unified git diff (patch), with no explanations or markdown.
    """
    client = OpenAI(api_key=api_key)

    formatted_files = "\n".join([f"File: {file.path}\nContents: {file.contents}" for file in relevant_files])
    user_prompt = (
        f"{problem_statement}\n\n"
        "Output ONLY the raw unified git diff (patch) code that implements the solution. "
        "Do NOT include any explanations, markdown, code blocks, or any extra text. "
        "Do NOT use triple backticks or any formatting. "
        "Do NOT say anything before or after the diff. "
        "Return ONLY the code for the patch, starting with 'diff --git'. "
        "If you include anything else, the solution will be rejected. "
        "If you need to create a new file, use the correct unified diff format for new files, including '--- /dev/null', '+++ b/<filename>', and 'new file mode 100644'. "
        "Ensure the patch is valid and can be applied with 'git apply' without errors. The patch must end with a single newline and have no extra blank lines or trailing whitespace."
        "Here are the relevant files in the repository: " + "\n".join(formatted_files)
    )
    response = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=4000,
        messages=[
            {"role": "user", "content": user_prompt}
        ]
    )
    return response.choices[0].message.content


def validate_and_fix_patch_with_openai(problem_statement: str, relevant_files: List[File], failed_patch: str, error_message: str, api_key: str) -> str:
    """
    Uses OpenAI to validate and fix a patch that failed to apply.
    """
    client = OpenAI(api_key=api_key)

    formatted_files = "\n".join([f"File: {file.path}\nContents: {file.contents}" for file in relevant_files])
    user_prompt = (
        f"The following git patch failed to apply with this error: {error_message}\n\n"
        f"Original problem statement: {problem_statement}\n\n"
        f"Failed patch:\n{failed_patch}\n\n"
        "Please analyze the failed patch and generate a corrected version that will apply successfully. "
        "Output ONLY the raw unified git diff (patch) code that implements the solution. "
        "Do NOT include any explanations, markdown, code blocks, or any extra text. "
        "Do NOT use triple backticks or any formatting. "
        "Do NOT say anything before or after the diff. "
        "Return ONLY the code for the corrected patch, starting with 'diff --git'. "
        "If you include anything else, the solution will be rejected. "
        "Ensure the patch is valid and can be applied with 'git apply' without errors. The patch must end with a single newline and have no extra blank lines or trailing whitespace.\n"
        "Here are the relevant files in the repository: " + formatted_files
    )
    response = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=4000,
        messages=[
            {"role": "user", "content": user_prompt}
        ]
    )
    return response.choices[0].message.content 