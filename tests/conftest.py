"""
Global pytest configuration and fixtures for all tests.
This replaces the shell scripts with proper pytest setup.
"""
import pytest
import asyncio
import asyncpg
import os
import sys
from unittest.mock import patch, AsyncMock, Mock
from httpx import AsyncClient
import pytest_asyncio

# Ensure the project root is in PYTHONPATH for module imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'api', 'src')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Set environment variables for DBManager initialization
# These are used by backend/db_manager.py to create the new_db instance
os.environ.setdefault('AWS_MASTER_USERNAME', 'test_user')
os.environ.setdefault('AWS_MASTER_PASSWORD', 'test_pass')
os.environ.setdefault('AWS_RDS_PLATFORM_ENDPOINT', 'localhost')
os.environ.setdefault('AWS_RDS_PLATFORM_DB_NAME', 'postgres')
os.environ.setdefault('PGPORT', '5432')
os.environ.setdefault('POSTGRES_TEST_URL', 'postgresql://test_user:test_pass@localhost:5432/postgres')

# Import after setting environment variables and path
from backend.db_manager import DBManager, new_db
from main import app

# --- Pytest Hooks for Session-wide Setup/Teardown ---

def pytest_configure(config):
    """Register custom markers for tests."""
    config.addinivalue_line("markers", "slow: marks tests as slow")
    config.addinivalue_line("markers", "integration: marks tests as integration tests requiring database")
    config.addinivalue_line("markers", "unit: marks tests as unit tests (fast, no external dependencies)")
    config.addinivalue_line("markers", "endpoints: marks tests that test API endpoints")
    config.addinivalue_line("markers", "core: marks tests that test core business logic")

# --- Fixtures for Test Setup ---

@pytest_asyncio.fixture(scope="session")
async def postgres_service():
    """Ensure PostgreSQL is ready and yield control."""
    # This fixture is primarily for ensuring the service is up and healthy
    # The actual DB setup/teardown for tests is handled by db_setup
    print("\nWaiting for PostgreSQL service to be ready...")
    # Use a direct connection check to ensure it's truly ready for connections
    conn = None
    for _ in range(30): # Try for 30 seconds
        try:
            conn = await asyncpg.connect(
                user=os.getenv('POSTGRES_USER', 'test_user'),
                password=os.getenv('POSTGRES_PASSWORD', 'test_pass'),
                host=os.getenv('AWS_RDS_PLATFORM_ENDPOINT', 'localhost'),
                port=int(os.getenv('PGPORT', '5432')),
                database=os.getenv('POSTGRES_DB', 'postgres')
            )
            print("PostgreSQL service is ready.")
            break
        except (asyncpg.exceptions.PostgresError, OSError) as e:
            print(f"PostgreSQL not ready yet: {e}. Retrying...")
            await asyncio.sleep(1)
    if conn:
        await conn.close()
    else:
        pytest.fail("PostgreSQL service did not become ready.")
    yield

@pytest_asyncio.fixture(scope="session")
async def db_setup(postgres_service):
    """Setup test database for the entire test session and initialize DBManager."""
    # Ensure postgres_service is up before proceeding
    await postgres_service

    test_db_url = os.getenv('POSTGRES_TEST_URL')
    if not test_db_url:
        pytest.skip("POSTGRES_TEST_URL not set for integration tests.")

    # Connect to the default 'postgres' database to create/drop the test database
    base_url = test_db_url.rsplit('/', 1)[0]
    test_db_name = test_db_url.rsplit('/', 1)[1]

    conn_to_default_db = await asyncpg.connect(f"{base_url}/postgres")
    try:
        # Terminate all other connections to the test database before dropping
        await conn_to_default_db.execute(f"""
            SELECT pg_terminate_backend(pg_stat_activity.pid)
            FROM pg_stat_activity
            WHERE pg_stat_activity.datname = '{test_db_name}'
              AND pid <> pg_backend_pid();
        """)
        await conn_to_default_db.execute(f"DROP DATABASE IF EXISTS {test_db_name}")
        await conn_to_default_db.execute(f"CREATE DATABASE {test_db_name}")
    finally:
        await conn_to_default_db.close()

    # Initialize the global new_db instance with the test database
    # This ensures all application code uses the test database
    await new_db.open()
    
    yield # Tests run here

    # Cleanup after all tests in the session
    await new_db.close()
    conn_to_default_db = await asyncpg.connect(f"{base_url}/postgres")
    try:
        await conn_to_default_db.execute(f"""
            SELECT pg_terminate_backend(pg_stat_activity.pid)
            FROM pg_stat_activity
            WHERE pg_stat_activity.datname = '{test_db_name}'
              AND pid <> pg_backend_pid();
        """)
        await conn_to_default_db.execute(f"DROP DATABASE IF EXISTS {test_db_name}")
    finally:
        await conn_to_default_db.close()

@pytest_asyncio.fixture
async def db_conn(db_setup):
    """Provide a database connection for individual tests."""
    # Ensure db_setup is complete before providing a connection
    await db_setup
    async with new_db.acquire() as conn:
        yield conn

@pytest_asyncio.fixture
async def async_client():
    """Provide an asynchronous test client for FastAPI endpoints."""
    # For unit tests, we don't need a real server, just the app
    async with AsyncClient(app=app, base_url="http://testserver") as client:
        yield client

# --- Mock fixtures for unit tests ---

@pytest.fixture
def mock_db_manager():
    """Mock the DBManager for unit tests that don't need real database."""
    with patch('api.src.backend.db_manager.new_db') as mock_db:
        # Mock the acquire method to return a mock connection
        mock_conn = AsyncMock()
        mock_conn_context = AsyncMock()
        mock_conn_context.__aenter__.return_value = mock_conn
        mock_conn_context.__aexit__.return_value = None
        mock_db.acquire.return_value = mock_conn_context
        
        # Mock the pool attribute
        mock_db.pool = Mock()
        
        yield mock_db

@pytest.fixture
def mock_db_connection():
    """Mock database connection for unit tests."""
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock()
    mock_conn.fetch = AsyncMock()
    mock_conn.execute = AsyncMock()
    mock_conn.transaction = AsyncMock()
    
    # Mock transaction context
    mock_transaction = AsyncMock()
    mock_transaction.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_transaction.__aexit__ = AsyncMock(return_value=None)
    mock_conn.transaction.return_value = mock_transaction
    
    return mock_conn