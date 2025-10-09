import os
import utils.logger as logger

from dotenv import load_dotenv



# Load everything from .env
load_dotenv()



# Load the environment configuration
ENV = os.getenv("ENV")
if not ENV:
    logger.fatal("ENV is not set in .env")

if ENV != "prod" and ENV != "dev":
    logger.fatal("ENV must be either 'prod' or 'dev'")



# Load AWS configuration
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
if not AWS_ACCESS_KEY_ID:
    logger.fatal("AWS_ACCESS_KEY_ID is not set in .env")
    
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
if not AWS_SECRET_ACCESS_KEY:
    logger.fatal("AWS_SECRET_ACCESS_KEY is not set in .env")

AWS_REGION = os.getenv("AWS_REGION")
if not AWS_REGION:
    logger.fatal("AWS_REGION is not set in .env")



# Load S3 configuration
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
if not S3_BUCKET_NAME:
    logger.fatal("S3_BUCKET_NAME is not set in .env")



# Load database configuration
DATABASE_USERNAME = os.getenv("DATABASE_USERNAME")
if not DATABASE_USERNAME:
    logger.fatal("DATABASE_USERNAME is not set in .env")
    
DATABASE_PASSWORD = os.getenv("DATABASE_PASSWORD")
if not DATABASE_PASSWORD:
    logger.fatal("DATABASE_PASSWORD is not set in .env")
    
DATABASE_HOST = os.getenv("DATABASE_HOST")
if not DATABASE_HOST:
    logger.fatal("DATABASE_HOST is not set in .env")
    
DATABASE_PORT = int(os.getenv("DATABASE_PORT"))
if not DATABASE_PORT:
    logger.fatal("DATABASE_PORT is not set in .env")
    
DATABASE_NAME = os.getenv("DATABASE_NAME")
if not DATABASE_NAME:
    logger.fatal("DATABASE_NAME is not set in .env")



# Load screener configuration
SCREENER_PASSWORD = os.getenv("SCREENER_PASSWORD")
if not SCREENER_PASSWORD:
    logger.fatal("SCREENER_PASSWORD is not set in .env")