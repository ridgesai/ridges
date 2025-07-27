"""
Proxy package for Chutes API integration with database validation.

This package provides:
- FastAPI app for proxying requests to Chutes API
- Database models and connection management
- ChutesClient for API interactions
- Configuration constants
"""

# Load environment variables first, before any other imports
import os
from dotenv import load_dotenv

# Get the directory of this file and resolve the .env path correctly
current_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(current_dir, ".env")
load_dotenv(env_path)

# Models
from .models import (
    SandboxStatus,
    EvaluationRun,
    GPTMessage,
    EmbeddingRequest,
    InferenceRequest,
    Embedding,
    Inference,
)

# Database
from .database import (
    db_manager,
    get_evaluation_run_by_id,
    create_embedding,
    update_embedding,
    create_inference,
    update_inference,
    get_total_embedding_cost,
    get_total_inference_cost,
    DBManager,
)

# Chutes client and providers
from .chutes_client import ChutesClient
from .providers import InferenceManager

# Configuration
from .config import (
    CHUTES_API_KEY,
    CHUTES_EMBEDDING_URL,
    CHUTES_INFERENCE_URL,
    MODEL_PRICING,
    MAX_COST_PER_RUN,
    DEFAULT_MODEL,
    SERVER_HOST,
    SERVER_PORT,
    LOG_LEVEL,
)

__all__ = [
    # Models
    "SandboxStatus",
    "EvaluationRun", 
    "GPTMessage",
    "EmbeddingRequest",
    "InferenceRequest",
    "Embedding",
    "Inference",
    
    # Database
    "db_manager",
    "get_evaluation_run_by_id",
    "create_embedding",
    "update_embedding",
    "create_inference",
    "update_inference",
    "get_total_cost_for_run",
    "DBManager",
    
    # Client
    "ChutesClient",
    
    # Config
    "CHUTES_API_KEY",
    "CHUTES_EMBEDDING_URL", 
    "CHUTES_INFERENCE_URL",
    "MODEL_PRICING",
    "MAX_COST_PER_RUN",
    "DEFAULT_MODEL",
    "SERVER_HOST",
    "SERVER_PORT",
    "LOG_LEVEL",
] 