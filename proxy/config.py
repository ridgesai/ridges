import os
from typing import Dict, Literal

ENV: Literal["prod", "staging", "dev"] = os.getenv("ENV", "prod")

# Chutes API configuration
CHUTES_API_KEY = os.getenv("CHUTES_API_KEY", "")
CHUTES_EMBEDDING_URL = "https://chutes-baai-bge-large-en-v1-5.chutes.ai/embed"
CHUTES_INFERENCE_URL = "https://llm.chutes.ai/v1/chat/completions"
# Targon API configuration (for fallback)
TARGON_API_KEY = os.getenv("TARGON_API_KEY", "")

# Pricing configuration
EMBEDDING_PRICE_PER_SECOND = 0.0001

# Models that support Targon fallback
TARGON_FALLBACK_MODELS = {
    "moonshotai/Kimi-K2-Instruct"
}

# Targon-specific pricing (per million tokens)
TARGON_PRICING: Dict[str, float] = {
    "moonshotai/Kimi-K2-Instruct": 0.14,  # $0.14/M input, $2.49/M output - using input rate for now
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