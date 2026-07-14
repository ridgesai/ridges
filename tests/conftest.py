from __future__ import annotations

import base64
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import bittensor
import pytest
from testcontainers.postgres import PostgresContainer

from utils.database import deinitialize_database, initialize_database

# upload.py instantiates Subtensor at module level; replace the class before
# any test module is collected so the WebSocket connection is never attempted.
bittensor.Subtensor = MagicMock

REPO_ROOT = Path(__file__).resolve().parents[1]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

TEST_ENV_DEFAULTS = {
    "AWS_ACCESS_KEY_ID": "test",
    "AWS_REGION": "us-east-1",
    "AWS_SECRET_ACCESS_KEY": "test",
    "BURN": "false",
    "COLDKEY_BAN_ADMIN_API_KEY": "test-coldkey-ban-admin-key",
    "DATABASE_HOST": "localhost",
    "DATABASE_NAME": "ridges_test",
    "DATABASE_PASSWORD": "ridges",
    "DATABASE_PORT": "5432",
    "DATABASE_USERNAME": "ridges",
    "DISALLOW_UPLOADS": "false",
    "ENV": "dev",
    "HOST": "0.0.0.0",
    "INCLUDE_SOLUTIONS": "false",
    "MINER_AGENT_UPLOAD_RATE_LIMIT_SECONDS": "60",
    "MODE": "screener",
    "NETUID": "1",
    "NUM_EVALS_PER_AGENT": "1",
    "OWNER_HOTKEY": "test-owner-hotkey",
    "PORT": "8000",
    "PRUNE_THRESHOLD": "0.4",
    "REQUEST_EVALUATION_INTERVAL_SECONDS": "10",
    "RIDGES_INFERENCE_GATEWAY_URL": "http://10.0.0.1:8001",
    "RIDGES_AGENT_KEY_ENCRYPTION_KEY": base64.b64encode(b"k" * 32).decode("ascii"),
    "RIDGES_PLATFORM_URL": "http://localhost:8000",
    "S3_BUCKET_NAME": "ridges-test",
    "SCREENER_1_THRESHOLD": "0.4",
    "SCREENER_2_THRESHOLD": "0.4",
    "SCREENER_NAME": "screener-1-1",
    "SCREENER_PASSWORD": "test",
    "SEND_HEARTBEAT_INTERVAL_SECONDS": "10",
    "SET_WEIGHTS_INTERVAL_SECONDS": "60",
    "SET_WEIGHTS_TIMEOUT_SECONDS": "90",
    "SHOULD_RUN_LOOPS": "false",
    "SIMULATE_EVALUATION_RUNS": "false",
    "SIMULATE_EVALUATION_RUN_MAX_TIME_PER_STAGE_SECONDS": "1",
    "SUBTENSOR_ADDRESS": "ws://localhost:9944",
    "SUBTENSOR_NETWORK": "local",
    "UPDATE_AUTOMATICALLY": "false",
    "UPLOAD_SEND_ADDRESS": "test-upload-address",
    "VALIDATOR_HEARTBEAT_TIMEOUT_INTERVAL_SECONDS": "60",
    "VALIDATOR_HEARTBEAT_TIMEOUT_SECONDS": "60",
    "VALIDATOR_MAX_EVALUATION_RUN_LOG_SIZE_BYTES": "1048576",
    "VALIDATOR_RUNNING_AGENT_TIMEOUT_SECONDS": "60",
    "VALIDATOR_RUNNING_EVAL_TIMEOUT_SECONDS": "60",
    "AGENT_UUID_NAMESPACE": "37c807fa-a7d2-4ac5-985b-e5ad6bbbc1c9",
}

for key, value in TEST_ENV_DEFAULTS.items():
    os.environ.setdefault(key, value)


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="module")
async def postgres_db():
    with PostgresContainer("postgres:16") as container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(5432)

        saved = {
            k: os.environ.get(k)
            for k in (
                "DATABASE_USERNAME",
                "DATABASE_PASSWORD",
                "DATABASE_HOST",
                "DATABASE_PORT",
                "DATABASE_NAME",
            )
        }
        os.environ.update(
            {
                "DATABASE_USERNAME": container.username,
                "DATABASE_PASSWORD": container.password,
                "DATABASE_HOST": host,
                "DATABASE_PORT": str(port),
                "DATABASE_NAME": container.dbname,
            }
        )

        try:
            await initialize_database(
                username=container.username,
                password=container.password,
                host=host,
                port=port,
                name=container.dbname,
            )
            yield
        finally:
            await deinitialize_database()
            for key, val in saved.items():
                if val is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = val
