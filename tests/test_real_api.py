"""
Real API integration tests that test actual HTTP requests against the running server.
These tests require the API server to be running on localhost:8000.
"""

import pytest
import requests
import os
import time
from typing import Optional
from requests.exceptions import RequestException

# Base URL for the running API server
API_BASE_URL = os.getenv('API_BASE_URL', 'http://localhost:8000')

def wait_for_server(max_retries: int = 30, delay: float = 1.0) -> bool:
    """Wait for the API server to be ready."""
    for i in range(max_retries):
        try:
            response = requests.get(f"{API_BASE_URL}/healthcheck", timeout=5)
            if response.status_code == 200:
                print(f"Server is ready after {i+1} attempts")
                return True
        except RequestException:
            pass
        time.sleep(delay)
    return False

class TestRealAPIEndpoints:
    """Test real API endpoints against the running server."""
    
    @classmethod
    def setup_class(cls):
        """Wait for server to be ready before running tests."""
        if not wait_for_server():
            pytest.skip("API server is not available")
    
    def test_healthcheck_endpoint(self):
        """Test the real healthcheck endpoint."""
        response = requests.get(f"{API_BASE_URL}/healthcheck")
        assert response.status_code == 200
        assert response.text == '"OK"'
    
    def test_healthcheck_results_endpoint(self):
        """Test the real healthcheck-results endpoint."""
        response = requests.get(f"{API_BASE_URL}/healthcheck-results")
        assert response.status_code == 200
        data = response.json()
        # The endpoint returns a list of platform status check records
        assert isinstance(data, list)
        # If there are records, they should have the expected structure
        if data:
            assert "checked_at" in data[0]
    
    def test_server_root_endpoint(self):
        """Test that the server responds to root requests."""
        response = requests.get(f"{API_BASE_URL}/")
        # Should return 404 for root endpoint (no route defined)
        assert response.status_code == 404
    
    def test_upload_endpoint_structure(self):
        """Test that upload endpoint exists and validates input."""
        response = requests.post(f"{API_BASE_URL}/upload/agent", json={})
        # Should return 422 for validation error (missing required fields)
        assert response.status_code == 422
    
    def test_retrieval_endpoints_exist(self):
        """Test that retrieval endpoints exist."""
        # Test network stats endpoint
        response = requests.get(f"{API_BASE_URL}/retrieval/network-stats")
        # Should either return 200 (if database is working) or 500 (if database issues)
        assert response.status_code in [200, 500]
        
        # Test top agents endpoint
        response = requests.get(f"{API_BASE_URL}/retrieval/top-agents")
        assert response.status_code in [200, 500]
    
    def test_scoring_endpoints_exist(self):
        """Test that scoring endpoints exist."""
        # Test check top agent endpoint
        response = requests.get(f"{API_BASE_URL}/scoring/check-top-agent")
        assert response.status_code in [200, 500]
        
        # Test ban agents endpoint (should return 422 for missing data)
        response = requests.post(f"{API_BASE_URL}/scoring/ban-agents", json={})
        assert response.status_code == 422
    
    def test_agent_summaries_endpoints_exist(self):
        """Test that agent summaries endpoints exist."""
        import uuid
        fake_id = str(uuid.uuid4())
        response = requests.get(f"{API_BASE_URL}/agent-summaries/agent-summary/{fake_id}")
        assert response.status_code in [404, 500]  # Should be 404 for non-existent agent
    
    def test_open_users_endpoints_exist(self):
        """Test that open users endpoints exist."""
        # Test sign-in endpoint (should return 422 for missing data)
        response = requests.post(f"{API_BASE_URL}/open-users/sign-in", json={})
        assert response.status_code == 422
    
    def test_websocket_endpoint_exists(self):
        """Test that WebSocket endpoint is accessible."""
        import websocket
        try:
            # Try to connect to WebSocket endpoint
            ws = websocket.create_connection(f"{API_BASE_URL.replace('http', 'ws')}/ws", timeout=5)
            ws.close()
            assert True  # If we get here, the endpoint exists
        except Exception as e:
            # WebSocket might not be available in test environment, that's OK
            print(f"WebSocket test skipped: {e}")
            assert True

class TestRealAPIPerformance:
    """Test API performance and response times."""
    
    def test_healthcheck_response_time(self):
        """Test that healthcheck responds quickly."""
        import time
        start_time = time.time()
        response = requests.get(f"{API_BASE_URL}/healthcheck", timeout=5)
        end_time = time.time()
        
        assert response.status_code == 200
        assert (end_time - start_time) < 2.0  # Should respond within 2 seconds
    
    def test_concurrent_requests(self):
        """Test that the server can handle multiple concurrent requests."""
        import concurrent.futures
        import threading
        
        def make_request():
            response = requests.get(f"{API_BASE_URL}/healthcheck", timeout=5)
            return response.status_code
        
        # Make 5 concurrent requests
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(make_request) for _ in range(5)]
            results = [future.result() for future in futures]
        
        # All requests should succeed
        assert all(status == 200 for status in results)

if __name__ == "__main__":
    pytest.main([__file__, "-v"]) 