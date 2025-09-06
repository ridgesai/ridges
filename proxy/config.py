import os
from typing import Dict, Literal

ENV: Literal["prod", "staging", "dev"] = os.getenv("ENV", "prod")

# Chutes API configuration
CHUTES_API_KEY = os.getenv("CHUTES_API_KEY", "")
CHUTES_EMBEDDING_URL = "https://chutes-baai-bge-large-en-v1-5.chutes.ai/embed"
CHUTES_INFERENCE_URL = "https://llm.chutes.ai/v1/chat/completions"
# Targon API configuration (for fallback)
TARGON_API_KEY = os.getenv("TARGON_API_KEY", "")

# Authentication configuration
SCREENER_PASSWORD = os.getenv("SCREENER_PASSWORD", "")

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

# Pricing configuration
EMBEDDING_PRICE_PER_SECOND = 0.0001

MODEL_PRICING: Dict[str, float] = {
    "deepseek-ai/DeepSeek-V3-0324": 0.2722,
    "agentica-org/DeepCoder-14B-Preview": 0.02,
    "deepseek-ai/DeepSeek-V3": 0.2722,
    "deepseek-ai/DeepSeek-R1": 0.2722,
    "deepseek-ai/DeepSeek-R1-0528": 0.2722,
    "NousResearch/DeepHermes-3-Mistral-24B-Preview": 0.1411,
    "NousResearch/DeepHermes-3-Llama-3-8B-Preview": 0.224,
    "chutesai/Llama-4-Maverick-17B-128E-Instruct-FP8": 0.2722,
    "Qwen/Qwen3-32B": 0.0272,
    "Qwen/Qwen3-235B-A22B-Instruct-2507": 0.0000,
    "Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8": 0.1999,
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
    "rayonlabs/Gradients-Instruct-8B": 0.02
}

# Models that support Targon fallback
TARGON_FALLBACK_MODELS = {
    "moonshotai/Kimi-K2-Instruct",
    "zai-org/GLM-4.5-FP8",  # Will be redirected to GLM-4.5
    "Qwen/Qwen3-235B-A22B-Instruct-2507"
}

# Targon-specific pricing (per million tokens)
TARGON_PRICING: Dict[str, float] = {
    "moonshotai/Kimi-K2-Instruct": 0.14,
    "zai-org/GLM-4.5-FP8": 0.5,  # Same pricing as FP8 variant
    "Qwen/Qwen3-235B-A22B-Instruct-2507": 0.12
      # $0.14/M input, $2.49/M output - using input rate for now
}

# Model redirects (map requested model to actual model for Targon)
MODEL_REDIRECTS: Dict[str, str] = {
    "zai-org/GLM-4.5-FP8": "zai-org/GLM-4.5"  # Redirect FP8 to standard GLM-4.5
}

# Cost limits
MAX_COST_PER_RUN = 2.0  # Maximum cost per evaluation run

# Default model
DEFAULT_MODEL = "moonshotai/Kimi-K2-Instruct"
DEFAULT_TEMPERATURE = 0.7

# Server configuration
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8001"))

# Logging configuration
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")