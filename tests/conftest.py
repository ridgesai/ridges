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

# Database initialization will be handled by fixtures

# Import after setting environment variables and path
# Only import these if we're running integration tests
# For unit tests, we'll import them lazily when needed

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
    for _ in range(60): # Try for 60 seconds (increased timeout)
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
            print(f"PostgreSQL not ready yet: {e}. Retrying... ({_+1}/60)")
            await asyncio.sleep(2)  # Increased sleep time
    if conn:
        await conn.close()
    else:
        pytest.fail("PostgreSQL service did not become ready.")
    return True

@pytest_asyncio.fixture(scope="session")
async def db_setup(postgres_service):
    """Setup test database for the entire test session and initialize DBManager."""
    # Ensure postgres_service is up before proceeding
    # postgres_service is now a boolean indicating readiness

    test_db_url = os.getenv('POSTGRES_TEST_URL')
    if not test_db_url:
        pytest.skip("POSTGRES_TEST_URL not set for integration tests.")

    # Import lazily to avoid issues in unit tests
    from api.src.backend.db_manager import new_db

    # Initialize the global new_db instance with the test database
    # This ensures all application code uses the test database
    try:
        await new_db.open()
        print("Database connection pool opened successfully")
    except Exception as e:
        print(f"Error opening database connection pool: {e}")
        pytest.fail(f"Failed to initialize database connection pool: {e}")
    
    # Setup database schema for integration tests
    try:
        async with new_db.acquire() as conn:
            await setup_database_schema(conn)
        print("Database schema setup completed")
    except Exception as e:
        print(f"Error setting up database schema: {e}")
        pytest.fail(f"Failed to setup database schema: {e}")
    
    yield True # Tests run here

    # Cleanup after all tests in the session
    try:
        await new_db.close()
        print("Database connection pool closed successfully")
    except Exception as e:
        print(f"Error closing database connection pool: {e}")

@pytest_asyncio.fixture(scope="function")
async def db_conn(db_setup):
    """Provide a database connection for each test function."""
    from api.src.backend.db_manager import new_db
    
    # Ensure the connection pool is initialized
    if not new_db.pool:
        await new_db.open()
    
    async with new_db.acquire() as conn:
        yield conn

async def setup_database_schema(conn: asyncpg.Connection):
    """Setup database schema for integration tests"""
    # Read the actual production schema file
    schema_path = os.path.join(os.path.dirname(__file__), '..', 'api', 'src', 'backend', 'postgres_schema.sql')
    with open(schema_path, 'r') as f:
        schema_sql = f.read()
    
    # Execute the production schema
    await conn.execute(schema_sql)
    
    # Disable the approval deletion trigger for tests to allow cleanup
    await conn.execute("""
        DROP TRIGGER IF EXISTS no_delete_approval_trigger ON approved_version_ids;
        CREATE OR REPLACE FUNCTION prevent_delete_approval_test() RETURNS TRIGGER AS $$ 
        BEGIN 
            -- Allow deletions in test environment
            RETURN OLD; 
        END; 
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER no_delete_approval_trigger BEFORE DELETE ON approved_version_ids 
        FOR EACH ROW EXECUTE FUNCTION prevent_delete_approval_test();
    """)
    
    # Insert test evaluation sets for testing
    await conn.execute("""
        INSERT INTO evaluation_sets (set_id, type, swebench_instance_id) VALUES 
        (1, 'screener-1', 'test_instance_1'),
        (1, 'screener-2', 'test_instance_2'), 
        (1, 'validator', 'test_instance_3')
        ON CONFLICT DO NOTHING
    """)



@pytest_asyncio.fixture
async def async_client():
    """Provide an asynchronous test client for FastAPI endpoints."""
    # For unit tests, we don't need a real server, just the app
    from api.src.main import app
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