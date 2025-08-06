# Database Integration Testing Setup

This document provides instructions for setting up a comprehensive testing environment with real PostgreSQL database integration for the Ridges API endpoints.

**✅ Uses Production Schema**: The integration tests now use the actual `postgres_schema.sql` file to ensure testing against the exact production database structure, including all triggers, materialized views, and constraints.

## Prerequisites

1. **PostgreSQL Database Server**
   - Local PostgreSQL installation or Docker container
   - PostgreSQL 12+ recommended
   - Superuser access to create/drop test databases

2. **Python Dependencies**
   ```bash
   uv add pytest pytest-asyncio asyncpg httpx pytest-postgresql
   ```

## Setup Options

### Option 1: Local PostgreSQL Installation

1. **Install PostgreSQL locally:**
   ```bash
   # macOS with Homebrew
   brew install postgresql
   brew services start postgresql
   
   # Ubuntu/Debian
   sudo apt-get install postgresql postgresql-contrib
   sudo systemctl start postgresql
   
   # Create test user and database
   sudo -u postgres createuser --superuser test_user
   sudo -u postgres psql -c "ALTER USER test_user PASSWORD 'test_password';"
   ```

2. **Set environment variable:**
   ```bash
   export POSTGRES_TEST_URL="postgresql://test_user:test_password@localhost:5432/ridges_test"
   ```

### Option 2: Docker PostgreSQL (Recommended)

1. **Create docker-compose.test.yml:**
   ```yaml
   version: '3.8'
   services:
     postgres-test:
       image: postgres:15
       environment:
         POSTGRES_DB: postgres
         POSTGRES_USER: test_user
         POSTGRES_PASSWORD: test_password
       ports:
         - "5433:5432"  # Different port to avoid conflicts
       volumes:
         - postgres_test_data:/var/lib/postgresql/data
       tmpfs:
         - /tmp
         - /var/run/postgresql

   volumes:
     postgres_test_data:
   ```

2. **Start test database:**
   ```bash
   docker-compose -f tests/docker-compose.test.yml up -d postgres-test
   ```

3. **Set environment variable:**
   ```bash
   export POSTGRES_TEST_URL="postgresql://test_user:test_password@localhost:5433/ridges_test"
   ```

### Option 3: pytest-postgresql (Automatic)

This uses an ephemeral PostgreSQL instance that's automatically managed:

1. **Install pytest-postgresql:**
   ```bash
   uv add pytest-postgresql
   ```

2. **Use the auto-managed fixture in tests** (see Alternative Test Configuration below)

## Running the Tests

### Basic Test Execution

```bash
# Run all integration tests
uv run python -m pytest test_endpoints_integration.py -v

# Run specific test class
uv run python -m pytest test_endpoints_integration.py::TestUploadEndpoints -v

# Run with coverage
uv run python -m pytest test_endpoints_integration.py --cov=api/src/endpoints --cov-report=html

# Run tests in parallel (faster)
uv add pytest-xdist
uv run python -m pytest test_endpoints_integration.py -n auto
```

### Test Database Management

The test suite automatically:
- Creates a clean test database for each test session
- Sets up the required schema and tables
- Provides transaction isolation for each test
- Cleans up after all tests complete

### Environment Variables Required

```bash
# Test database connection
export POSTGRES_TEST_URL="postgresql://test_user:test_password@localhost:5432/ridges_test"

# Application environment variables (mocked in tests)
export AWS_MASTER_USERNAME="test_user"
export AWS_MASTER_PASSWORD="test_pass" 
export AWS_RDS_PLATFORM_ENDPOINT="test_endpoint"
export AWS_RDS_PLATFORM_DB_NAME="test_db"
```

## Test Configuration Files

### Create pytest.ini
```ini
[tool:pytest]
testpaths = .
python_files = test_*.py
python_classes = Test*
python_functions = test_*
asyncio_mode = auto
addopts = 
    -v
    --tb=short
    --strict-markers
    --disable-warnings
markers =
    slow: marks tests as slow
    integration: marks tests as integration tests
    unit: marks tests as unit tests
```

### Create .env.test
```bash
# Database Configuration
POSTGRES_TEST_URL=postgresql://test_user:test_password@localhost:5432/ridges_test

# Mock AWS Configuration  
AWS_MASTER_USERNAME=test_user
AWS_MASTER_PASSWORD=test_pass
AWS_RDS_PLATFORM_ENDPOINT=test_endpoint
AWS_RDS_PLATFORM_DB_NAME=test_db

# Test-specific settings
TESTING=true
LOG_LEVEL=INFO
```

## Alternative Test Configuration (pytest-postgresql)

If you prefer fully automated database management, replace the database setup in the test file:

```python
import pytest_postgresql

# Use pytest-postgresql for automatic database management
@pytest.fixture(scope="session") 
def postgresql_proc():
    return pytest_postgresql.postgresql_proc(
        port=None,
        unixsocketdir='/tmp'
    )

@pytest.fixture(scope="session")
def postgresql(postgresql_proc):
    return pytest_postgresql.postgresql('postgresql_proc')

@pytest.fixture
async def db_conn(postgresql):
    """Database connection with automatic cleanup"""
    import asyncpg
    conn = await asyncpg.connect(
        host=postgresql.info.host,
        port=postgresql.info.port,
        user=postgresql.info.user,
        database=postgresql.info.dbname
    )
    
    try:
        # Setup schema
        await setup_test_schema(conn)
        yield conn
    finally:
        await conn.close()
```

## Test Data Management

### Test Data Isolation
- Each test runs in its own database transaction
- Transactions are automatically rolled back after each test
- No test data persists between test runs

### Test Data Factories
Consider creating test data factories for complex objects:

```python
class AgentFactory:
    @staticmethod
    async def create_agent(conn, **kwargs):
        defaults = {
            'version_id': uuid.uuid4(),
            'miner_hotkey': f'test_miner_{uuid.uuid4().hex[:8]}',
            'agent_name': 'test_agent',
            'version_num': 1,
            'status': 'awaiting_screening_1'
        }
        defaults.update(kwargs)
        
        await conn.execute("""
            INSERT INTO miner_agents (version_id, miner_hotkey, agent_name, version_num, status)
            VALUES ($1, $2, $3, $4, $5)
        """, defaults['version_id'], defaults['miner_hotkey'], defaults['agent_name'],
             defaults['version_num'], defaults['status'])
        
        return defaults
```

## Continuous Integration Setup

### GitHub Actions (.github/workflows/test.yml)
```yaml
name: Integration Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    
    services:
      postgres:
        image: postgres:15
        env:
          POSTGRES_PASSWORD: test_password
          POSTGRES_USER: test_user
          POSTGRES_DB: postgres
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
        ports:
          - 5432:5432

    steps:
    - uses: actions/checkout@v3
    
    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.11'
    
    - name: Install uv
      run: curl -LsSf https://astral.sh/uv/install.sh | sh
    
    - name: Install dependencies
      run: uv sync
    
    - name: Run tests
      run: uv run python -m pytest test_endpoints_integration.py -v
      env:
        POSTGRES_TEST_URL: postgresql://test_user:test_password@localhost:5432/ridges_test
```

## Performance Considerations

### Speed Optimizations
- Use connection pooling in tests
- Run tests in parallel with `pytest-xdist`
- Use in-memory databases for unit tests
- Use real databases only for integration tests

### Resource Management
- Limit concurrent database connections
- Use smaller test datasets when possible
- Clean up large test data promptly

## Troubleshooting

### Common Issues

1. **Connection Refused**
   - Check PostgreSQL is running: `pg_isready`
   - Verify connection parameters
   - Check firewall settings

2. **Permission Denied**
   - Ensure test user has CREATE DATABASE privileges
   - Check database ownership

3. **Schema Errors**
   - Verify all required tables are created
   - Check for missing foreign key constraints
   - Ensure materialized views are refreshed

4. **Test Isolation Issues**
   - Verify transaction rollback is working
   - Check for connection leaks
   - Ensure proper cleanup in fixtures

### Debug Commands
```bash
# Check database connection
psql $POSTGRES_TEST_URL -c "SELECT 1"

# View test database schema
psql $POSTGRES_TEST_URL -c "\dt"

# Check test data
psql $POSTGRES_TEST_URL -c "SELECT COUNT(*) FROM miner_agents"

# Run tests with debug output
uv run python -m pytest test_endpoints_integration.py -v -s --tb=long
```

## Best Practices

1. **Test Independence**: Each test should be able to run independently
2. **Data Cleanup**: Use transactions and fixtures for automatic cleanup
3. **Realistic Data**: Use data that mirrors production scenarios
4. **Performance**: Monitor test execution time and optimize slow tests
5. **Error Handling**: Test both success and failure scenarios
6. **Documentation**: Document complex test setups and data requirements

This testing setup provides comprehensive coverage of your API endpoints with real database integration, ensuring that your application works correctly with actual SQL operations and constraints.