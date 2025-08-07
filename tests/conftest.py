"""
Global pytest configuration and fixtures for all tests.
This replaces the shell scripts with proper pytest setup.
"""
import os
import sys
import asyncio
import pytest
import subprocess
import time
from pathlib import Path

# Add project root to Python path to fix import issues
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

def pytest_configure(config):
    """Configure pytest environment to match GitHub Actions exactly."""
    # Set environment variables exactly like GitHub Actions
    os.environ.update({
        'POSTGRES_TEST_URL': 'postgresql://test_user:test_pass@localhost:5432/postgres',
        'AWS_MASTER_USERNAME': 'test_user',
        'AWS_MASTER_PASSWORD': 'test_pass',
        'AWS_RDS_PLATFORM_ENDPOINT': 'localhost',
        'AWS_RDS_PLATFORM_DB_NAME': 'postgres',
        'PGPORT': '5432',
        'PYTHONPATH': str(project_root)
    })

@pytest.fixture(scope="session", autouse=True)
def setup_test_services():
    """Start PostgreSQL service for integration tests."""
    # Check if we're in GitHub Actions (services are already running)
    if os.getenv('GITHUB_ACTIONS'):
        yield
        return
        
    # For local testing, start Docker services
    docker_compose_file = Path(__file__).parent / "docker-compose.test.yml"
    
    try:
        # Start PostgreSQL service
        subprocess.run([
            "docker-compose", "-f", str(docker_compose_file), 
            "up", "-d", "postgres-test"
        ], check=True, capture_output=True)
        
        # Wait for PostgreSQL to be ready
        time.sleep(10)
        
        # Verify PostgreSQL is accessible
        result = subprocess.run([
            "docker-compose", "-f", str(docker_compose_file),
            "exec", "-T", "postgres-test", 
            "pg_isready", "-U", "test_user", "-d", "postgres"
        ], capture_output=True)
        
        if result.returncode != 0:
            pytest.skip("PostgreSQL not ready for integration tests")
            
        yield
        
    except subprocess.CalledProcessError as e:
        pytest.skip(f"Failed to start test services: {e}")
        
    finally:
        # Cleanup
        try:
            subprocess.run([
                "docker-compose", "-f", str(docker_compose_file), "down"
            ], check=True, capture_output=True)
        except subprocess.CalledProcessError:
            pass  # Ignore cleanup errors

def pytest_collection_modifyitems(config, items):
    """Add markers to tests based on their file and content."""
    for item in items:
        # Mark tests based on file patterns
        if "test_miner_agent_flow.py" in str(item.fspath):
            item.add_marker(pytest.mark.unit)
            item.add_marker(pytest.mark.core)
        elif "test_endpoints_integration.py" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
            item.add_marker(pytest.mark.endpoints)
        elif "test_endpoints" in str(item.fspath):
            item.add_marker(pytest.mark.endpoints)
            
        # Mark slow tests
        if "integration" in str(item.fspath) or "slow" in item.name:
            item.add_marker(pytest.mark.slow)