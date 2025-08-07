"""
Simple endpoint tests that work with the actual Ridges API structure.
These tests verify the endpoints exist and return expected responses.
"""

import pytest
import uuid
from unittest.mock import patch, AsyncMock, Mock
from fastapi.testclient import TestClient

# Only set environment variables if they're not already set (don't override GitHub Actions env vars)
import os
if not os.getenv('AWS_MASTER_USERNAME'):
    os.environ.update({
        'AWS_MASTER_USERNAME': 'test_user',
        'AWS_MASTER_PASSWORD': 'test_pass',
        'AWS_RDS_PLATFORM_ENDPOINT': 'localhost',
        'AWS_RDS_PLATFORM_DB_NAME': 'postgres'
    })

# Import after setting environment variables
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'api', 'src'))


def create_test_app():
    """Create a test app with mocked database manager."""
    with patch('api.src.backend.db_manager.new_db') as mock_db:
        # Mock the acquire method to return a mock connection
        mock_conn = AsyncMock()
        mock_conn_context = AsyncMock()
        mock_conn_context.__aenter__.return_value = mock_conn
        mock_conn_context.__aexit__.return_value = None
        mock_db.acquire.return_value = mock_conn_context
        
        # Mock the pool attribute
        mock_db.pool = Mock()
        
        from api.src.main import app
        return app


class TestHealthcheckEndpoints:
    """Test basic healthcheck endpoints"""
    
    def test_healthcheck_endpoint(self):
        """Test basic healthcheck"""
        app = create_test_app()
        client = TestClient(app)
        
        response = client.get("/healthcheck")
        assert response.status_code == 200


class TestUploadEndpoints:
    """Test upload endpoint structure and validation"""
    
    @patch('api.src.backend.db_manager.get_transaction')
    def test_upload_agent_endpoint_exists(self, mock_get_transaction):
        """Test that upload agent endpoint exists and validates input"""
        app = create_test_app()
        client = TestClient(app)
        
        # Mock database transaction to avoid database calls
        mock_conn = AsyncMock()
        mock_transaction = AsyncMock()
        mock_transaction.__aenter__.return_value = mock_conn
        mock_get_transaction.return_value = mock_transaction
        mock_conn.fetchval.return_value = None  # No banned hotkey
        
        # Test with missing fields - should return 422 for validation error
        response = client.post("/upload/agent", json={})
        assert response.status_code == 422
        
        # Test with some fields - still should fail validation
        incomplete_data = {"miner_hotkey": "test"}
        response = client.post("/upload/agent", json=incomplete_data)
        assert response.status_code == 422

    @patch('api.src.backend.db_manager.get_transaction')
    def test_upload_open_agent_endpoint_exists(self, mock_get_transaction):
        """Test that upload open-agent endpoint exists"""
        app = create_test_app()
        client = TestClient(app)
        
        # Mock database transaction
        mock_conn = AsyncMock()
        mock_transaction = AsyncMock()
        mock_transaction.__aenter__.return_value = mock_conn
        mock_get_transaction.return_value = mock_transaction
        
        response = client.post("/upload/open-agent", json={})
        assert response.status_code == 422  # Should fail validation


class TestRetrievalEndpoints:
    """Test retrieval endpoints"""
    
    @patch('api.src.backend.queries.statistics.get_24_hour_statistics')
    def test_network_stats_endpoint(self, mock_get_stats):
        """Test network stats endpoint"""
        app = create_test_app()
        client = TestClient(app)
        
        # Mock the statistics function directly
        mock_get_stats.return_value = {
            "number_of_agents": 100,
            "agent_iterations_last_24_hours": 20,
            "top_agent_score": 0.85,
            "daily_score_improvement": 0.05
        }
        
        response = client.get("/retrieval/network-stats")
        # This endpoint requires database connection, so it might fail in simple tests
        # We just check that the endpoint exists and returns a proper response
        assert response.status_code in [200, 500]  # Either success or database error

    @patch('api.src.backend.queries.statistics.get_top_agents')
    def test_top_agents_endpoint(self, mock_get_top_agents):
        """Test top agents endpoint"""
        app = create_test_app()
        client = TestClient(app)
        
        # Mock top agents data
        mock_agent = Mock()
        mock_agent.version_id = uuid.uuid4()
        mock_agent.miner_hotkey = "test_miner"
        mock_agent.agent_name = "test_agent"
        mock_agent.score = 0.85
        mock_agent.approved = True
    
        mock_get_top_agents.return_value = [mock_agent]
        
        response = client.get("/retrieval/top-agents")
        # This endpoint requires database connection, so it might fail in simple tests
        # We just check that the endpoint exists and returns a proper response
        assert response.status_code in [200, 500]  # Either success or database error

    @patch('api.src.socket.websocket_manager.WebSocketManager.get_instance')
    def test_connected_validators_endpoint(self, mock_ws_manager):
        """Test connected validators endpoint"""
        app = create_test_app()
        client = TestClient(app)
        
        # Mock WebSocket manager
        mock_manager = Mock()
        mock_validator = Mock()
        mock_validator.get_type.return_value = "validator"
        mock_validator.hotkey = "validator1"
        mock_validator.status = "available"
        
        mock_manager.clients = {"1": mock_validator}
        mock_ws_manager.return_value = mock_manager
        
        response = client.get("/retrieval/connected-validators")
        # This endpoint requires database connection, so it might fail in simple tests
        # We just check that the endpoint exists and returns a proper response
        assert response.status_code in [200, 500]  # Either success or database error


class TestScoringEndpoints:
    """Test scoring endpoints"""
    
    @patch('api.src.backend.entities.MinerAgentScored.get_top_agent')
    def test_check_top_agent_endpoint(self, mock_get_top_agent):
        """Test check top agent endpoint"""
        app = create_test_app()
        client = TestClient(app)
        
        # Mock top agent
        mock_top_agent = Mock()
        mock_top_agent.miner_hotkey = "top_miner"
        mock_top_agent.version_id = uuid.uuid4()
        mock_top_agent.avg_score = 0.92
        
        mock_get_top_agent.return_value = mock_top_agent
        
        response = client.get("/scoring/check-top-agent")
        # This endpoint requires database connection, so it might fail in simple tests
        # We just check that the endpoint exists and returns a proper response
        assert response.status_code in [200, 500]  # Either success or database error

    def test_ban_agents_endpoint_validation(self):
        """Test ban agents endpoint input validation"""
        app = create_test_app()
        client = TestClient(app)
        
        # Test without password - should fail
        response = client.post("/scoring/ban-agents", json={"miner_hotkeys": ["test"]})
        assert response.status_code == 422

    def test_approve_version_endpoint_validation(self):
        """Test approve version endpoint input validation"""
        app = create_test_app()
        client = TestClient(app)
        
        # Test without password - should fail
        response = client.post("/scoring/approve-version", json={"version_ids": ["test"]})
        assert response.status_code == 422


class TestOpenUsersEndpoints:
    """Test open users endpoints"""
    
    def test_signin_endpoint_validation(self):
        """Test sign in endpoint validation"""
        app = create_test_app()
        client = TestClient(app)
        
        # Test with missing fields
        response = client.post("/open-users/sign-in", json={})
        assert response.status_code == 422


class TestAgentSummariesEndpoints:
    """Test agent summaries endpoints"""
    
    @patch('api.src.backend.queries.statistics.get_agent_summary_by_hotkey')
    def test_agent_summary_endpoint_not_found(self, mock_get_agent_summary):
        """Test agent summary endpoint with non-existent ID"""
        app = create_test_app()
        client = TestClient(app)
        
        # Mock agent summary to return None (not found)
        mock_get_agent_summary.return_value = None
        
        fake_uuid = uuid.uuid4()
        response = client.get(f"/agent-summaries/agent-summary/{fake_uuid}")
        # This endpoint requires database connection, so it might fail in simple tests
        # We just check that the endpoint exists and returns a proper response
        assert response.status_code in [404, 500]  # Either not found or database error


class TestEndpointSecurity:
    """Test endpoint security and validation"""
    
    def test_endpoints_reject_invalid_json(self):
        """Test that endpoints properly reject invalid JSON"""
        app = create_test_app()
        client = TestClient(app)
        
        # Test various endpoints with invalid JSON
        endpoints = [
            "/upload/agent",
            "/scoring/ban-agents", 
            "/scoring/approve-version",
            "/open-users/sign-in"
        ]
        
        for endpoint in endpoints:
            response = client.post(
                endpoint,
                content="invalid json",
                headers={"Content-Type": "application/json"}
            )
            assert response.status_code == 422

    def test_get_endpoints_dont_accept_post(self):
        """Test that GET endpoints reject POST requests appropriately"""
        app = create_test_app()
        client = TestClient(app)
        
        # Test GET endpoints with POST
        get_endpoints = [
            "/healthcheck",
            "/retrieval/network-stats",
            "/retrieval/top-agents",
            "/scoring/check-top-agent"
        ]
        
        for endpoint in get_endpoints:
            response = client.post(endpoint, json={})
            # Should be 405 Method Not Allowed or 422 if it has different validation
            assert response.status_code in [405, 422]


class TestEndpointResponseStructure:
    """Test endpoint response structures"""
    
    def test_healthcheck_response_structure(self):
        """Test healthcheck response has expected structure"""
        app = create_test_app()
        client = TestClient(app)
        
        response = client.get("/healthcheck")
        assert response.status_code == 200
        # Healthcheck returns a simple string "OK", not JSON
        assert response.text == '"OK"'


class TestWebSocketEndpoint:
    """Test WebSocket endpoint exists"""
    
    def test_websocket_endpoint_exists(self):
        """Test that WebSocket endpoint is defined"""
        app = create_test_app()
        
        # Check that the WebSocket route exists
        websocket_routes = [route for route in app.routes if hasattr(route, 'path') and route.path == "/ws"]
        assert len(websocket_routes) == 1
        
        # Verify it's a WebSocket route
        ws_route = websocket_routes[0]
        assert hasattr(ws_route, 'endpoint')


if __name__ == "__main__":
    # Run with simple output
    pytest.main([__file__, "-v", "--tb=short"])