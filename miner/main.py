from fastapi import FastAPI
from dashboard.logging_utils import get_logger

from miner.dependancies import get_config, Config
from miner.endpoints.codegen import router as codegen_router
from miner.endpoints.availability import router as availability_router

logger = get_logger(__name__)

app = FastAPI()

app.dependency_overrides[Config] = get_config()

# Include relevant miner routers 
app.include_router(
    codegen_router, 
    prefix="/codegen", 
    tags=["codegen"]
)

app.include_router(
    availability_router,
    tags=["availability"]
)