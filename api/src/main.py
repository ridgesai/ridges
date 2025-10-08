# TODO: cleanup how we set this all up
from dotenv import load_dotenv

load_dotenv("api/.env")

import asyncio
from fastapi import FastAPI, WebSocket
from fastapi.concurrency import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from api.src.backend.db_manager import new_db
from loggers.logging_utils import get_logger
from api.src.endpoints.upload import router as upload_router
from api.src.endpoints.retrieval import router as retrieval_router
from api.src.endpoints.scoring import router as scoring_router
from api.src.endpoints.healthcheck import router as healthcheck_router
from api.src.endpoints.system_status import router as system_status_router
from api.src.endpoints.agents import router as agents_router
from api.src.endpoints.open_users import router as open_user_router
from api.src.endpoints.benchmarks import router as benchmarks_router
from api.src.endpoints.validator import router as validator_router
from api.src.utils.config import WHITELISTED_VALIDATOR_IPS

logger = get_logger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await new_db.open()
    
    # Check IP whitelist configuration at startup
    if not WHITELISTED_VALIDATOR_IPS:
        logger.warning("⚠️" * 5)
        logger.warning("⚠️  IP whitelist is empty - allowing ALL IPs to access protected endpoints!")
        logger.warning("⚠️  This is a SECURITY RISK for production environments!")
        logger.warning("⚠️  Add IPs to 'whitelist' array in whitelist.json to restrict access.")
        logger.warning("⚠️" * 5)
    else:
        logger.info(f"✅ IP whitelist configured with {len(WHITELISTED_VALIDATOR_IPS)} whitelisted IPs")

    
    
    # Recover threshold-based approvals
    from api.src.utils.threshold_scheduler import threshold_scheduler
    await threshold_scheduler.recover_pending_approvals()

    yield

    await new_db.close()

app = FastAPI(lifespan=lifespan)


# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", 'https://www.ridges.ai'],
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

app.include_router(upload_router, prefix="/upload")
app.include_router(retrieval_router, prefix="/retrieval")
app.include_router(scoring_router, prefix="/scoring")
app.include_router(agents_router, prefix="/agents")
app.include_router(open_user_router, prefix="/open-users")
app.include_router(benchmarks_router, prefix="/benchmarks")
app.include_router(validator_router, prefix="/validator")
app.include_router(system_status_router, prefix="/system")
app.include_router(healthcheck_router)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, ws_ping_timeout=None, ws_max_size=32 * 1024 * 1024)
