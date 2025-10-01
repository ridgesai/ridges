AGENT_RATE_LIMIT_SECONDS = 60 * 60 * 18 # 18 hours
PERMISSABLE_PACKAGES = [
    "aiohttp",
    "ast",
    "asyncio",
    "catboost",
    "collections",
    "collections.abc",
    "concurrent.futures",
    "copy",
    "cv2",
    "datasets",
    "difflib",
    "dill",
    "django",
    "gensim",
    "glob",
    "inspect",
    "io",
    "joblib",
    "json",
    "keras",
    "lightgbm",
    "math",
    "matplotlib",
    "mlflow",
    "nltk",
    "numpy",
    "optuna",
    "os",
    "pandas",
    "pathlib",
    "pickle_mixin",
    "pydantic",
    "pytest",
    "random",
    "re",
    "requests",
    "scikit-learn",
    "scipy",
    "seaborn",
    "sentence_transformers",
    "sklearn",
    "sklearn.feature_extraction.text",
    "sklearn.feature_extraction.text.TfidfVectorizer",
    "socket",
    "spacy",
    "statsmodels",
    "subprocess",
    "sys",
    "tensorflow",
    "textwrap",
    "time",
    "tokenize",
    "tqdm",
    "torch",
    "torchaudio",
    "torchvision",
    "traceback",
    "transformers",
    "typing",
    "urllib.error",
    "urllib.parse",
    "urllib.request",
    "urllib3",
    "xgboost",
    "autogen",
    "autogen_ext",
    "autogen_agentchat"
]
MODEL_PRICE_PER_1M_TOKENS = {   "deepseek-ai/DeepSeek-V3-0324": 0.2722,
                                "agentica-org/DeepCoder-14B-Preview": 0.02,
                                "deepseek-ai/DeepSeek-V3": 0.2722,
                                "deepseek-ai/DeepSeek-R1": 0.2722,
                                "deepseek-ai/DeepSeek-R1-0528": 0.2722,
                                "NousResearch/DeepHermes-3-Mistral-24B-Preview": 0.1411,
                                "NousResearch/DeepHermes-3-Llama-3-8B-Preview": 0.224,
                                "chutesai/Llama-4-Maverick-17B-128E-Instruct-FP8": 0.2722,
                                "Qwen/Qwen3-32B": 0.0272,
                                "Qwen/QwQ-32B": 0.0151,
                                "chutesai/Mistral-Small-3.2-24B-Instruct-2506": 0.0302,
                                "unsloth/gemma-3-27b-it": 0.1568,
                                "agentica-org/DeepCoder-14B-Preview": 0.0151,
                                "THUDM/GLM-Z1-32B-0414": 0.0302,
                                "ArliAI/QwQ-32B-ArliAI-RpR-v1": 0.0151,
                                "Qwen/Qwen3-30B-A3B": 0.0302,
                                "hutesai/Devstral-Small-2505": 0.0302,
                                "chutesai/Mistral-Small-3.1-24B-Instruct-2503": 0.0272,
                                "chutesai/Llama-4-Scout-17B-16E-Instruct": 0.0302,
                                "shisa-ai/shisa-v2-llama3.3-70b": 0.0302,
                                "moonshotai/Kimi-Dev-72B": 0.1008,
                                "moonshotai/Kimi-K2-Instruct": 0.5292,
                                "all-hands/openhands-lm-32b-v0.1": 0.0246,
                                "sarvamai/sarvam-m": 0.0224,
                                "zai-org/GLM-4.5-FP8": 0.2000,
                                "zai-org/GLM-4.5-Air": 0.0000,
                                "rayonlabs/Gradients-Instruct-8B": 0.02,
                                "openai/gpt-oss-120b": 0.2904,
}
EMBEDDING_PRICE_PER_SECOND = 0.0001
SCREENING_1_THRESHOLD = 0.4
SCREENING_2_THRESHOLD = 0.3
PRUNE_THRESHOLD = 0.05 # Must be within 5 percentage points of the final score

# Authentication configuration
import os

def load_whitelist():
    """Load IP whitelist from JSON file"""
    import json
    try:
        with open('whitelist.json', 'r') as f:
            data = json.load(f)
            return set(data.get('whitelist', []))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()

# Parse whitelisted IPs once at startup
WHITELISTED_VALIDATOR_IPS = load_whitelist()
