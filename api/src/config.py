import os

DB_USER = os.getenv("AWS_MASTER_USERNAME")
DB_PASS = os.getenv("AWS_MASTER_PASSWORD")
DB_HOST = os.getenv("AWS_RDS_PLATFORM_ENDPOINT")
DB_NAME = os.getenv("AWS_RDS_PLATFORM_DB_NAME")
DB_PORT = os.getenv("PGPORT", "5432")

def ensure_env_vars_exist():
    assert DB_USER is not None, "DB_USER environment variable is required"
    assert DB_PASS is not None, "DB_PASS environment variable is required"
    assert DB_HOST is not None, "DB_HOST environment variable is required"
    assert DB_NAME is not None, "DB_NAME environment variable is required"