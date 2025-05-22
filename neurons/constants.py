from typing import Final

from ridges.helpers.classes import IngestionHeuristics

NO_MINER_RESPONSE_SCORE: float = 0.005
UPLOAD_ISSUE_ENDPOINT: Final[str] = "https://gh-issue-pull.onrender.com/upload_issue"
OPEN_ISSUE_ENDPOINT: Final[str] = "https://gh-issue-pull.onrender.com/open_issue"
DOCKER_CACHE_LEVEL: Final[str] = "instance"

LOG_SESSION_CONTEXT: int = 26

## Validator eval constants
LLM_EVAL_MULT: Final[float] = 9.0
PROCESS_TIME_MULT: Final[float] = 1.0

class ValidatorDefaults:
    CODINGTASK_TIMEOUT_MINS = 10.0
    MODEL = "gpt4omini"
    INGESTION_HEURISTICS = IngestionHeuristics(
        min_files_to_consider_dir_for_problems=3,
        min_file_content_len=50,
    )
    NEURON_NUM_CONCURRENT_FORWARDS: Final[int] = 2
    MAX_MINERS_PER_PROBLEM: Final[int] = 15
    NO_RESPONSE_MIN: Final[float] = 0.005
