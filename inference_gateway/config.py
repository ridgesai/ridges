import os
import utils.logger as logger

from dotenv import load_dotenv



load_dotenv()



HOST = os.getenv("HOST")
if not HOST:
    logger.fatal("HOST is not set in .env")

PORT = os.getenv("PORT")
if not PORT:
    logger.fatal("PORT is not set in .env")
PORT = int(PORT)



USE_DATABASE = os.getenv("USE_DATABASE")
if not USE_DATABASE:
    logger.fatal("USE_DATABASE is not set in .env")
USE_DATABASE = USE_DATABASE.lower() == "true"

if USE_DATABASE:
    DATABASE_USERNAME = os.getenv("DATABASE_USERNAME")
    if not DATABASE_USERNAME:
        logger.fatal("DATABASE_USERNAME is not set in .env")
    
    DATABASE_PASSWORD = os.getenv("DATABASE_PASSWORD")
    if not DATABASE_PASSWORD:
        logger.fatal("DATABASE_PASSWORD is not set in .env")
    
    DATABASE_HOST = os.getenv("DATABASE_HOST")
    if not DATABASE_HOST:
        logger.fatal("DATABASE_HOST is not set in .env")
    
    DATABASE_PORT = os.getenv("DATABASE_PORT")
    if not DATABASE_PORT:
        logger.fatal("DATABASE_PORT is not set in .env")
    DATABASE_PORT = int(DATABASE_PORT)
    
    DATABASE_NAME = os.getenv("DATABASE_NAME")
    if not DATABASE_NAME:
        logger.fatal("DATABASE_NAME is not set in .env")

    CHECK_EVALUATION_RUNS = os.getenv("CHECK_EVALUATION_RUNS")
    if not CHECK_EVALUATION_RUNS:
        logger.fatal("CHECK_EVALUATION_RUNS is not set in .env")
    CHECK_EVALUATION_RUNS = CHECK_EVALUATION_RUNS.lower() == "true"



MAX_COST_PER_EVALUATION_RUN_USD = os.getenv("MAX_COST_PER_EVALUATION_RUN_USD")
if not MAX_COST_PER_EVALUATION_RUN_USD:
    logger.fatal("MAX_COST_PER_EVALUATION_RUN_USD is not set in .env")
MAX_COST_PER_EVALUATION_RUN_USD = float(MAX_COST_PER_EVALUATION_RUN_USD)



USE_CHUTES = os.getenv("USE_CHUTES")
if not USE_CHUTES:
    logger.fatal("USE_CHUTES is not set in .env")
USE_CHUTES = USE_CHUTES.lower() == "true"

if USE_CHUTES:
    CHUTES_INFERENCE_BASE_URL = os.getenv("CHUTES_INFERENCE_BASE_URL")
    if not CHUTES_INFERENCE_BASE_URL:
        logger.fatal("CHUTES_INFERENCE_BASE_URL is not set in .env")

    CHUTES_EMBEDDING_BASE_URL = os.getenv("CHUTES_EMBEDDING_BASE_URL")
    if not CHUTES_EMBEDDING_BASE_URL:
        logger.fatal("CHUTES_EMBEDDING_BASE_URL is not set in .env")

    CHUTES_API_KEY = os.getenv("CHUTES_API_KEY")
    if not CHUTES_API_KEY:
        logger.fatal("CHUTES_API_KEY is not set in .env")

    CHUTES_WEIGHT = os.getenv("CHUTES_WEIGHT")
    if not CHUTES_WEIGHT:
        logger.fatal("CHUTES_WEIGHT is not set in .env")
    CHUTES_WEIGHT = int(CHUTES_WEIGHT)



USE_TARGON = os.getenv("USE_TARGON")
if not USE_TARGON:
    logger.fatal("USE_TARGON is not set in .env")
USE_TARGON = USE_TARGON.lower() == "true"

if USE_TARGON:
    TARGON_BASE_URL = os.getenv("TARGON_BASE_URL")
    if not TARGON_BASE_URL:
        logger.fatal("TARGON_BASE_URL is not set in .env")

    TARGON_API_KEY = os.getenv("TARGON_API_KEY")
    if not TARGON_API_KEY:
        logger.fatal("TARGON_API_KEY is not set in .env")

    TARGON_WEIGHT = os.getenv("TARGON_WEIGHT")
    if not TARGON_WEIGHT:
        logger.fatal("TARGON_WEIGHT is not set in .env")
    TARGON_WEIGHT = int(TARGON_WEIGHT)

USE_OPENROUTER = os.getenv("USE_OPENROUTER")
if not USE_OPENROUTER:
    logger.fatal("USE_OPENROUTER is not set in .env")
USE_OPENROUTER = USE_OPENROUTER.lower() == "true"

if USE_OPENROUTER:
    OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL")
    if not OPENROUTER_BASE_URL:
        logger.fatal("OPENROUTER_BASE_URL is not set in .env")

    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
    if not OPENROUTER_API_KEY:
        logger.fatal("OPENROUTER_API_KEY is not set in .env")

    OPENROUTER_WEIGHT = os.getenv("OPENROUTER_WEIGHT")
    if not OPENROUTER_WEIGHT:
        logger.fatal("OPENROUTER_WEIGHT is not set in .env")
    OPENROUTER_WEIGHT = int(OPENROUTER_WEIGHT)



if not USE_CHUTES and not USE_TARGON and not USE_OPENROUTER:
    logger.fatal("Either USE_CHUTES or USE_TARGON or USE_OPENROUTER must be set to True in .env")


TEST_INFERENCE_MODELS = os.getenv("TEST_INFERENCE_MODELS")
if not TEST_INFERENCE_MODELS:
    logger.fatal("TEST_INFERENCE_MODELS is not set in .env")
TEST_INFERENCE_MODELS = TEST_INFERENCE_MODELS.lower() == "true"

TEST_EMBEDDING_MODELS = os.getenv("TEST_EMBEDDING_MODELS")
if not TEST_EMBEDDING_MODELS:
    logger.fatal("TEST_EMBEDDING_MODELS is not set in .env")
TEST_EMBEDDING_MODELS = TEST_EMBEDDING_MODELS.lower() == "true"



logger.info("=== Inference Gateway Configuration ===")

logger.info(f"Host: {HOST}")
logger.info(f"Port: {PORT}")
logger.info("---------------------------------------")

if USE_DATABASE:
    logger.info(f"Database Username: {DATABASE_USERNAME}")
    logger.info(f"Database Host: {DATABASE_HOST}")
    logger.info(f"Database Port: {DATABASE_PORT}")
    logger.info(f"Database Name: {DATABASE_NAME}")
    if not CHECK_EVALUATION_RUNS:
        logger.warning("Not Checking Evaluation Runs!")
else:
    logger.warning("Not Using Database!")
logger.info("---------------------------------------")

if USE_CHUTES:
    logger.info("Using Chutes")
    logger.info(f"Chutes Inference Base URL: {CHUTES_INFERENCE_BASE_URL}")
    logger.info(f"Chutes Embedding Base URL: {CHUTES_EMBEDDING_BASE_URL}")
    logger.info(f"Chutes Weight: {CHUTES_WEIGHT}")
else:
    logger.warning("Not Using Chutes!")
logger.info("---------------------------------------")

if USE_TARGON:
    logger.info("Using Targon")
    logger.info(f"Targon Base URL: {TARGON_BASE_URL}")
    logger.info(f"Targon Weight: {TARGON_WEIGHT}")
else:
    logger.warning("Not Using Targon!")

if USE_OPENROUTER:
    logger.info("Using OpenRouter")
    logger.info(f"OpenRouter Base URL: {OPENROUTER_BASE_URL}")
    logger.info(f"OpenRouter Weight: {OPENROUTER_WEIGHT}")
else:
    logger.warning("Not Using OpenRouter!")

logger.info("=======================================")