# Ridges Test Suite

This directory contains comprehensive tests for the Ridges miner agent evaluation system.

**All testing-related files are now organized in this `tests/` directory for better project structure.**

## Test Files & Configuration

### Test Files
- **`test_miner_agent_flow.py`** - Core business logic and state transitions (34 tests, all passing ✅)
- **`test_endpoints_simple.py`** - Basic endpoint validation and structure (9/15 passing ✅)
- **`test_endpoints_sqlite.py`** - **CI-friendly integration tests** with SQLite (✅ **Recommended for CI**)
- **`test_endpoints_integration.py`** - Full database integration testing with **production schema**
- **`test_endpoints_unit.py`** - Detailed endpoint unit tests with mocking

### Configuration Files
- **`pytest.ini`** - Test configuration, markers, and settings
- **`docker-compose.test.yml`** - PostgreSQL test database Docker setup
- **`init-test-db.sql`** - Database initialization script for Docker
- **`github-actions-test.yml`** - **GitHub Actions workflow** for CI/CD
- **`TEST_SETUP_INSTRUCTIONS.md`** - Comprehensive setup guide for integration testing
- **`README.md`** - This documentation file

## Quick Start

### Run Core Tests (Recommended)
```bash
# From the project root
cd tests && uv run python -m pytest test_miner_agent_flow.py -v

# Or from project root using full path
uv run python -m pytest tests/test_miner_agent_flow.py -v
```

### Run Endpoint Tests
```bash  
# From tests directory
cd tests && uv run python -m pytest test_endpoints_simple.py -v

# Or from project root
uv run python -m pytest tests/test_endpoints_simple.py -v
```

### Run All Working Tests
```bash
# From tests directory (recommended)
cd tests && uv run python -m pytest test_miner_agent_flow.py test_endpoints_simple.py -v

# Or from project root
uv run python -m pytest tests/test_miner_agent_flow.py tests/test_endpoints_simple.py -v
```

### Run SQLite Integration Tests (CI-Friendly) ⭐
```bash
# Perfect for CI environments - no Docker required
cd tests && uv run python -m pytest test_endpoints_sqlite.py -v

# Or from project root
uv run python -m pytest tests/test_endpoints_sqlite.py -v
```

## Integration Testing Setup

For full integration testing with real database:

### 1. Start Test Database
```bash
# Start PostgreSQL test instance
docker-compose -f tests/docker-compose.test.yml up -d postgres-test
```

### 2. Set Environment Variable
```bash
export POSTGRES_TEST_URL="postgresql://test_user:test_password@localhost:5433/ridges_test"
```

### 3. Run Integration Tests
```bash
# From tests directory
cd tests && uv run python -m pytest test_endpoints_integration.py -v

# Or from project root
uv run python -m pytest tests/test_endpoints_integration.py -v
```

## Test Coverage

### ✅ Core Logic Tests (34 tests)
- **Agent Status Transitions** - Multi-stage screening flow validation
- **Screener Logic** - Stage detection, state management, atomic operations
- **Evaluation Flows** - Complete lifecycle from upload to scoring
- **Scoring Algorithms** - High score detection, leadership rules
- **Database Models** - Entity validation and business logic

### ✅ Endpoint Structure Tests (9/15 passing)
- **API Validation** - Input validation, error handling
- **Security** - Invalid JSON rejection, method validation
- **Response Structure** - Basic response format verification
- **WebSocket** - Endpoint existence verification

### ✅ SQLite Integration Tests (CI-Optimized)
- **Production-Like Schema** - SQLite schema mirroring PostgreSQL production
- **Zero Dependencies** - No Docker or external services required
- **Fast Execution** - In-memory database with transaction rollback
- **GitHub Actions Ready** - Perfect for continuous integration

### 🔧 PostgreSQL Integration Testing (Production Schema)
- **Real Database Operations** - Uses actual `postgres_schema.sql` production schema
- **Transaction Isolation** - Proper test data cleanup with rollback
- **Performance Testing** - Connection pooling, materialized views, query optimization  
- **Error Scenarios** - Database failure handling with production constraints

## Test Categories

### Unit Tests
- Fast execution (< 1 second)
- No external dependencies
- Focus on business logic
- Mock all I/O operations

### Integration Tests  
- Real database operations
- Full endpoint-to-database flow
- Transaction rollback for isolation
- Requires PostgreSQL setup

### API Tests
- HTTP request/response validation
- Authentication and authorization
- Input validation and sanitization
- Error handling and status codes

## Commands Reference

```bash
# Install test dependencies
uv add pytest pytest-asyncio

# Run specific test file
uv run python -m pytest tests/test_miner_agent_flow.py -v

# Run tests with coverage
uv run python -m pytest tests/ --cov=api/src --cov-report=html

# Run tests in parallel (faster)
uv add pytest-xdist
uv run python -m pytest tests/ -n auto

# Run only failing tests from last run
uv run python -m pytest tests/ --lf

# Run tests matching pattern
uv run python -m pytest tests/ -k "status_transition" -v

# Run tests with detailed output
uv run python -m pytest tests/ -v -s --tb=long
```

## Best Practices Implemented

- **Test Isolation** - Each test runs independently
- **Proper Mocking** - External dependencies mocked appropriately  
- **Data Factories** - Reusable test data creation
- **Error Coverage** - Both success and failure scenarios tested
- **Performance** - Fast execution with minimal setup
- **Documentation** - Clear test descriptions and assertions

## Continuous Integration

The tests are designed to work in CI environments. Core logic tests require no external setup, while integration tests can use Docker services in CI.

Example GitHub Actions workflow:
```yaml
- name: Run Tests
  run: |
    uv run python -m pytest tests/test_miner_agent_flow.py tests/test_endpoints_simple.py -v
```

For full integration testing in CI, use the Docker setup provided in `docker-compose.test.yml`.

## GitHub Actions Setup

Copy `tests/github-actions-test.yml` to `.github/workflows/test.yml` in your repository root:

```bash
# Create GitHub Actions directory and copy workflow
mkdir -p .github/workflows
cp tests/github-actions-test.yml .github/workflows/test.yml
```

**This workflow provides:**
- ✅ **Fast CI Tests**: Core logic + SQLite integration (always run)
- ✅ **Optional PostgreSQL Tests**: Full integration tests (on main branch or with label)
- ✅ **Coverage Reporting**: Automated coverage reports to Codecov
- ✅ **Linting & Type Checking**: Code quality validation

**Workflow Features:**
- **No Docker Required**: Uses SQLite for fast, reliable CI testing
- **PostgreSQL When Needed**: Full integration tests when specifically requested
- **Parallel Jobs**: Fast execution with parallel test and lint jobs
- **Smart Triggers**: Lightweight tests on PRs, full tests on main branch