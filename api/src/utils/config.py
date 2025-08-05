import requests
AGENT_RATE_LIMIT_SECONDS = 60 * 60 * 12
PERMISSABLE_PACKAGES = [
    "numpy",
    "pandas",
    "sklearn",
    "scipy",
    "matplotlib",
    "seaborn",
    "xgboost",
    "lightgbm",
    "catboost",
    "tensorflow",
    "keras",
    "torch",
    "torchvision",
    "torchaudio",
    "transformers",
    "datasets",
    "statsmodels",
    "nltk",
    "spacy",
    "gensim",
    "cv2",
    "mlflow",
    "optuna",
    "tqdm",
    "joblib",
    "dill",
    "pickle_mixin",
    "requests",
    "urllib3",
    "urllib.parse",
    "urllib.request",
    "urllib.error",
    "socket",
    "sentence_transformers",
    "sklearn.feature_extraction.text",
    "sklearn.feature_extraction.text.TfidfVectorizer",
    "concurrent.futures",
    "ast",
    "difflib",
    "pydantic"
]

# Fallback/default pricing.
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
                                "zai-org/GLM-4.5-Air": 0.0000
}
# Update from the actual model list, ensuring all models/prices are up-to-date.
try:
    response = requests.get("https://llm.chutes.ai/v1/models", timeout=5)
    if response.status_code == 200:
        data = response.json()
        for model in data.get("data", []):
            model_id = model.get("id")
            price_usd = model.get("pricing", {}).get("completion")
            if model_id and price_usd is not None:
                MODEL_PRICING[model_id] = price_usd
except Exception:
    pass

EMBEDDING_PRICE_PER_SECOND = 0.0001
SCREENING_THRESHOLD = 0.6
