# Testing Guide

This project uses pytest for all testing needs. Everything is configured to work automatically.

## Quick Start

```bash
# Run all tests (like GitHub Actions does)
cd tests
uv run python -m pytest

# Run only unit tests (fast)
uv run python -m pytest test_miner_agent_flow.py

# Run with coverage details
uv run python -m pytest --cov-report=term-missing

# Run specific test markers
uv run python -m pytest -m unit      # Unit tests only
uv run python -m pytest -m core      # Core business logic
uv run python -m pytest -m endpoints # API endpoint tests
```

## Test Categories

- **Unit Tests** (`test_miner_agent_flow.py`) - Core business logic, always reliable
- **Simple Tests** (`test_endpoints_simple.py`) - Basic API endpoint tests  
- **Unit Tests** (`test_endpoints_unit.py`) - API tests with mocking
- **Integration Tests** (`test_endpoints_integration.py`) - Full database integration

## Environment Setup

The `conftest.py` file automatically:
- Sets up PostgreSQL service for integration tests (Docker)
- Configures environment variables to match GitHub Actions
- Handles test markers and coverage reporting
- Manages Python path for proper imports

## Configuration Files

- `pytest.ini` - Main pytest configuration with coverage settings
- `conftest.py` - Global fixtures and test environment setup
- `docker-compose.test.yml` - PostgreSQL service for integration tests

## GitHub Actions

The workflow in `.github/workflows/testing.yml` simply runs:
```bash
cd tests
uv run python -m pytest
```

Everything else is handled by pytest configuration.