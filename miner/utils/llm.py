import openai

def generate_solution_with_openai(problem_statement: str, api_key: str) -> str:
    """
    Uses OpenAI to generate a code solution for the given problem statement.
    """
    client = openai.OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You are a helpful coding assistant."},
            {"role": "user", "content": problem_statement}
        ]
    )
    return response.choices[0].message.content 