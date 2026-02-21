# TODO ADAM: slowly fixing this

import asyncio

from fastapi import FastAPI
from fastapi.concurrency import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from api.loops.validator_heartbeat_timeout import validator_heartbeat_timeout_loop


from queries.evaluation import set_all_unfinished_evaluation_runs_to_errored
from api.src.endpoints.upload import router as upload_router


# NEW fixed endpoints
from api.endpoints.validator import router as validator_router
from api.endpoints.debug import router as debug_router
from api.endpoints.agent import router as agent_router
from api.endpoints.evaluation_run import router as evaluation_run_router
from api.endpoints.evaluations import router as evaluations_router
from api.endpoints.evaluation_sets import router as evaluation_sets_router
from api.endpoints.scoring import router as scoring_router
from api.endpoints.statistics import router as statistics_router
from api.endpoints.retrieval import router as retrieval_router






import api.config as config

from utils.s3 import initialize_s3, deinitialize_s3
from utils.database import initialize_database, deinitialize_database



@asynccontextmanager
async def lifespan(app: FastAPI):
    # Database setup
    await initialize_database(
        username=config.DATABASE_USERNAME,
        password=config.DATABASE_PASSWORD,
        host=config.DATABASE_HOST,
        port=config.DATABASE_PORT,
        name=config.DATABASE_NAME
    )

    # S3 setup
    await initialize_s3(
        _bucket=config.S3_BUCKET_NAME,
        region=config.AWS_REGION,
        access_key_id=config.AWS_ACCESS_KEY_ID,
        secret_access_key=config.AWS_SECRET_ACCESS_KEY
    )

    # Loops
    if config.SHOULD_RUN_LOOPS:
        asyncio.create_task(validator_heartbeat_timeout_loop())



    # TODO ADAM: fix this, the error message isn't useful and it sets it to a 2xxx error when it should be a 3xxx error
    await set_all_unfinished_evaluation_runs_to_errored(
        error_message="Platform crashed while running this evaluation"
    )


    yield



    await deinitialize_database()
    await deinitialize_s3()



app = FastAPI(lifespan=lifespan)














# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", 'https://www.ridges.ai'],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

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


if __name__ == "__main__":
    uvicorn.run(app, host=config.HOST, port=config.PORT)