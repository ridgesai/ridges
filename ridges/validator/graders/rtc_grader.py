from dataclasses import asdict
from agentao.helpers.clients import LogContext
from agentao.helpers.helpers import calculate_price
from agentao.validator.graders.abstract_grader import GraderInterface, MinerSubmission
from logging import Logger
from typing import List, Final, Tuple
import openai
import os
from pydantic import BaseModel
import bert_score

RTC_PROMPT: Final[str] = """
You are an expert code reviewer and descriptor. You will be provided with a
git diff patch, and your job is to generate a problem statement that
describes what the patch is trying to solve.

The code repo that this patch applies to is {repo}.

The following is the git diff patch:
{patch}
"""

class RtcResponseFormat(BaseModel):
    inverse_prompt: str

class RtcGrader(GraderInterface):
    """
    Grader that evaluates a miner's patch based on semantic similarity to the problem statement.
    """
    def __init__(self, logger: Logger):
        self.logger = logger

    def inverse_prompt(self, patch: str, repo: str) -> Tuple[str, float]:
        OPENAI_CLIENT: Final[openai.Client] = openai.Client(api_key=os.getenv("OPENAI_API_KEY"))
        completion = OPENAI_CLIENT.beta.chat.completions.parse(
            model='gpt-4o-mini',
            messages=[
                {"role": "system", "content": RTC_PROMPT.format(patch=patch, repo=repo)},
            ],
            response_format=RtcResponseFormat,
        )
        inv_prompt = completion.choices[0].message.parsed.inverse_prompt

        prompt_tokens, completion_tokens = completion.usage.prompt_tokens, completion.usage.completion_tokens
        cost = calculate_price("gpt-4o-mini", prompt_tokens, completion_tokens)

        return (inv_prompt, cost)
    
    def grade(self, submissions: List[MinerSubmission], forward_pass_id: str) -> List[float]:
        problem_statements = [s.problem.problem_statement for s in submissions]
        inv_prompts = []
        total_cost = 0
        for s in submissions:
            inv_prompt, cost = self.inverse_prompt(s.solution.patch, s.repo)
            inv_prompts.append(inv_prompt)
            total_cost += cost

        self.logger.info(f"RTC generate: {total_cost}", extra=asdict(LogContext(
            log_type="lifecycle",
            event_type="openai_cost",
        )))

        _, _, F1 = bert_score.score(problem_statements, inv_prompts, lang='en')

        hotkey_grade = [(s.miner_hotkey, f) for s, f in zip(submissions, F1.tolist())]
        for hk, score in hotkey_grade:
            self.logger.info(f"miner {hk} got RTC score of {score}", extra=asdict(LogContext(
                    log_type="lifecycle",
                    event_type="rtc_score",
                    additional_properties={
                        "grade": score, 
                        "miner_hotkey": hk,
                        "forward_pass_id": forward_pass_id,
                    }
                )))

        return F1.tolist()
