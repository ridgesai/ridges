# TODO ADAM: slowly fixing this

import asyncio

from fastapi import FastAPI
from fastapi.concurrency import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from api.loops.validator_heartbeat_timeout import validator_heartbeat_timeout_loop


from api.queries.evaluation import set_all_unfinished_evaluation_runs_to_errored
from api.src.endpoints.upload import router as upload_router
from api.src.endpoints.retrieval import router as retrieval_router
from api.src.endpoints.scoring import router as scoring_router
from api.src.endpoints.open_users import router as open_user_router
from api.src.endpoints.validator import router as validator_router
from api.src.endpoints.evaluation_sets import router as evaluation_sets_router










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

    # Loop setup
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
app.include_router(open_user_router, prefix="/open-users")
app.include_router(validator_router, prefix="/validator")
app.include_router(evaluation_sets_router, prefix="/evaluation-sets")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, ws_ping_timeout=None, ws_max_size=32 * 1024 * 1024)
