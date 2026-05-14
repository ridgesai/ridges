# TODO ADAM: slowly fixing this

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

import uvicorn
from asgi_correlation_id import CorrelationIdMiddleware
from fastapi import FastAPI
from fastapi.concurrency import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware

import api.config as config
from api.endpoints.agent import router as agent_router
from api.endpoints.debug import router as debug_router
from api.endpoints.evaluation_run import router as evaluation_run_router
from api.endpoints.evaluation_sets import router as evaluation_sets_router
from api.endpoints.evaluations import router as evaluations_router
from api.endpoints.retrieval import router as retrieval_router
from api.endpoints.scoring import router as scoring_router
from api.endpoints.statistics import router as statistics_router
from api.endpoints.scaling import router as scaling_router
from api.endpoints.validator import router as validator_router
from api.loops.approval_projector import approval_projector_loop
from api.loops.pre_screening_judge import pre_screening_projector_loop
from api.loops.validator_heartbeat_timeout import validator_heartbeat_timeout_loop
from api.src.endpoints.upload import router as upload_router
from api.src.middleware.request_interceptor import RequestInterceptorMiddleware
from api.src.utils.sentry import initialize_sentry
from queries.evaluation import set_all_unfinished_evaluation_runs_to_errored
from utils.bittensor import subtensor_client
from utils.database import deinitialize_database, initialize_database
from utils.logger import setup_logging
from utils.s3 import deinitialize_s3, initialize_s3

logger = logging.getLogger("api")


def _start_background_task(
    background_tasks: set[asyncio.Task[None]],
    name: str,
    coroutine: Coroutine[Any, Any, None],
) -> None:
    background_task = asyncio.create_task(coroutine, name=name)
    background_tasks.add(background_task)

    def on_done(done_task: asyncio.Task[None]) -> None:
        background_tasks.discard(done_task)
        if done_task.cancelled():
            return
        if exc := done_task.exception():
            logger.error(f"Background task crashed: {name}: {type(exc).__name__}: {exc}")

    background_task.add_done_callback(on_done)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()

    background_tasks: set[asyncio.Task[None]] = set()

    await initialize_database(
        username=config.DATABASE_USERNAME,
        password=config.DATABASE_PASSWORD,
        host=config.DATABASE_HOST,
        port=config.DATABASE_PORT,
        name=config.DATABASE_NAME,
    )
    await initialize_s3(
        _bucket=config.S3_BUCKET_NAME,
        region=config.AWS_REGION,
        access_key_id=config.AWS_ACCESS_KEY_ID,
        secret_access_key=config.AWS_SECRET_ACCESS_KEY,
        _endpoint_url=config.S3_ENDPOINT_URL,
    )
    await subtensor_client.initialize()

    if config.SHOULD_RUN_LOOPS:
        _start_background_task(
            background_tasks,
            "validator_heartbeat_timeout_loop",
            validator_heartbeat_timeout_loop(),
        )

    if config.PRE_SCREENING_PROJECTOR_RUN_LOOP:
        _start_background_task(background_tasks, "pre_screening_projector_loop", pre_screening_projector_loop())

    if config.AUTO_APPROVAL_RUN_LOOP:
        _start_background_task(background_tasks, "approval_projector_loop", approval_projector_loop())

    if config.SHOULD_RUN_LOOPS:
        await set_all_unfinished_evaluation_runs_to_errored(
            error_message="Platform crashed while running this evaluation"
        )

    yield

    tasks_to_cancel = tuple(background_tasks)
    for background_task in tasks_to_cancel:
        background_task.cancel()
    await asyncio.gather(*tasks_to_cancel, return_exceptions=True)

    await deinitialize_database()
    await deinitialize_s3()
    await subtensor_client.close()


initialize_sentry()

app = FastAPI(lifespan=lifespan)

# Middleware registration order: last added = outermost (runs first on requests).
# CorrelationIdMiddleware must be outermost so the ID is set before RequestInterceptorMiddleware reads it.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://www.ridges.ai"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestInterceptorMiddleware)
app.add_middleware(CorrelationIdMiddleware)

app.include_router(upload_router, prefix="/upload")
app.include_router(retrieval_router, prefix="/retrieval")
app.include_router(scoring_router, prefix="/scoring")
app.include_router(validator_router, prefix="/validator")
app.include_router(evaluation_sets_router, prefix="/evaluation-sets")
app.include_router(debug_router, prefix="/debug")
app.include_router(agent_router, prefix="/agent")
app.include_router(evaluation_run_router, prefix="/evaluation-run")
app.include_router(evaluations_router, prefix="/evaluation")
app.include_router(statistics_router, prefix="/statistics")
app.include_router(scaling_router, prefix="/scaling")


if __name__ == "__main__":
    uvicorn.run(app, host=config.HOST, port=config.PORT)
