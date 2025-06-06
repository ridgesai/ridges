from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI
from shared.logging_utils import get_logger

from miner.dependancies import get_config, Config
from miner.endpoints.codegen import router as codegen_router
from miner.endpoints.regression import router as regression_router
from miner.endpoints.availability import router as availability_router

logger = get_logger(__name__)

miner_dir = Path(__file__).parent
env_path = miner_dir / ".env"
load_dotenv(env_path)

app = FastAPI()

app.dependency_overrides[Config] = get_config()

# Include relevant miner routers 
app.include_router(
    codegen_router, 
    prefix="/codegen", 
    tags=["codegen"]
)

app.include_router(
    regression_router,
    prefix="/regression", 
    tags=["regression"]
)

app.include_router(
    availability_router,
    tags=["availability"]
)
