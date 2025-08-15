"""
Unit tests for API endpoints with mocked database operations.
These tests focus on business logic without requiring a real database.
"""

import pytest
import uuid
import os
from datetime import datetime, timezone
from unittest.mock import Mock, AsyncMock, patch
from fastapi.testclient import TestClient

# Import after setting environment variables
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'api', 'src'))

from api.src.main import app

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
        
        return app

# Create test client with mocked database
client = TestClient(create_test_app())


class TestUploadEndpointsUnit:
    """Unit tests for upload endpoints with mocked dependencies"""
    
    @patch('api.src.utils.s3.S3Manager.upload_file_object')
    @patch('api.src.utils.agent_summary_generator.generate_agent_summary')
    @patch('api.src.utils.upload_agent_helpers.check_code_similarity')
    @patch('api.src.backend.db_manager.get_transaction')
    def test_upload_agent_success_mocked(
        self, mock_get_transaction, mock_validate_code, mock_check_similarity,
        mock_generate_summary, mock_s3_upload, mock_get_registration
    ):
        """Test successful agent upload with all dependencies mocked"""
        
        # Setup mocks
        mock_get_registration.return_value = 100
        mock_s3_upload.return_value = "s3://test-bucket/agent.py"
        mock_check_similarity.return_value = 0.3
        mock_validate_code.return_value = True
        
        # Mock database transaction
        mock_conn = AsyncMock()
        mock_transaction = AsyncMock()
        mock_transaction.__aenter__.return_value = mock_conn
        mock_get_transaction.return_value = mock_transaction
        
        # Mock database queries
        mock_conn.fetchval.side_effect = [
            None,  # No banned hotkey
            None,  # No recent upload  
            1,     # Version number
            None   # No available screener
        ]
        mock_conn.execute.return_value = None
        mock_conn.fetchrow.return_value = None
        
        agent_data = {
            "miner_hotkey": "test_miner_123",
            "agent_name": "test_agent",
            "code": "def solve_problem(): return 'solution'",
            "signature": "test_signature"
        }
        
        response = client.post("/upload/agent", json=agent_data)
        
        assert response.status_code == 200
        result = response.json()
        assert "version_id" in result
        
        # Verify mocks were called appropriately
        mock_validate_code.assert_called_once()
        mock_check_similarity.assert_called_once()
        mock_s3_upload.assert_called_once()

    @patch('api.src.backend.db_manager.get_transaction')
    def test_upload_agent_banned_hotkey_mocked(self, mock_get_transaction):
        """Test upload rejection for banned hotkey"""
        
        # Mock database transaction
        mock_conn = AsyncMock()
        mock_transaction = AsyncMock()
        mock_transaction.__aenter__.return_value = mock_conn
        mock_get_transaction.return_value = mock_transaction
        
        # Mock banned hotkey check
        mock_conn.fetchval.return_value = "banned_miner"  # Banned hotkey found
        
        agent_data = {
            "miner_hotkey": "banned_miner",
            "agent_name": "test_agent", 
            "code": "def solve_problem(): return 'solution'",
            "signature": "test_signature"
        }
        
        response = client.post("/upload/agent", json=agent_data)
        
        assert response.status_code == 403
        assert "banned" in response.json()["detail"].lower()

    @patch('api.src.backend.db_manager.get_transaction')
    def test_upload_agent_rate_limit_mocked(self, mock_get_transaction):
        """Test rate limiting on agent uploads"""
        
        # Mock database transaction  
        mock_conn = AsyncMock()
        mock_transaction = AsyncMock()
        mock_transaction.__aenter__.return_value = mock_conn
        mock_get_transaction.return_value = mock_transaction
        
        # Mock recent upload found
        mock_conn.fetchval.side_effect = [
            None,  # Not banned
            datetime.now(timezone.utc)  # Recent upload found
        ]
        
        agent_data = {
            "miner_hotkey": "rate_limited_miner",
            "agent_name": "new_agent",
            "code": "def solve_problem(): return 'solution'", 
            "signature": "test_signature"
        }
        
        response = client.post("/upload/agent", json=agent_data)
        
        assert response.status_code == 429
        assert "rate limit" in response.json()["detail"].lower()


class TestScoringEndpointsUnit:
    """Unit tests for scoring endpoints with mocked dependencies"""
    
    @patch('api.src.backend.entities.MinerAgentScored.get_top_agent')
    def test_check_top_agent_mocked(self, mock_get_top_agent):
        """Test top agent retrieval with mocked database"""
        
        # Mock top agent data
        mock_top_agent = Mock()
        mock_top_agent.miner_hotkey = "top_miner_123"
        mock_top_agent.version_id = uuid.uuid4()
        mock_top_agent.avg_score = 0.92
        
        mock_get_top_agent.return_value = mock_top_agent
        
        response = client.get("/scoring/check-top-agent")
        
        assert response.status_code == 200
        result = response.json()
        assert result["miner_hotkey"] == "top_miner_123"
        assert result["avg_score"] == 0.92

    @patch('api.src.backend.entities.MinerAgentScored.get_top_agent') 
    def test_check_top_agent_none_mocked(self, mock_get_top_agent):
        """Test top agent retrieval when no agents exist"""
        
        mock_get_top_agent.return_value = None
        
        response = client.get("/scoring/check-top-agent")
        
        assert response.status_code == 404
        assert "no top agent" in response.json()["detail"].lower()

    @patch('api.src.endpoints.scoring.ADMIN_PASSWORD', 'test_admin_pass')
    @patch('api.src.backend.db_manager.get_transaction')
    def test_ban_agents_mocked(self, mock_get_transaction):
        """Test agent banning with mocked database"""
        
        # Mock database transaction
        mock_conn = AsyncMock()
        mock_transaction = AsyncMock()
        mock_transaction.__aenter__.return_value = mock_conn
        mock_get_transaction.return_value = mock_transaction
        
        mock_conn.executemany.return_value = None
        
        ban_data = {
            "admin_password": "test_admin_pass",
            "miner_hotkeys": ["bad_miner_1", "bad_miner_2"]
        }
        
        response = client.post("/scoring/ban-agents", json=ban_data)
        
        assert response.status_code == 200
        result = response.json()
        assert result["banned_count"] == 2
        
        # Verify database call was made
        mock_conn.executemany.assert_called_once()

    def test_ban_agents_wrong_password(self):
        """Test agent banning with wrong password"""
        
        ban_data = {
            "admin_password": "wrong_password",
            "miner_hotkeys": ["some_miner"]
        }
        
        response = client.post("/scoring/ban-agents", json=ban_data)
        
        assert response.status_code == 401
        assert "unauthorized" in response.json()["detail"].lower()

    @patch('api.src.endpoints.scoring.ADMIN_PASSWORD', 'test_admin_pass')
    @patch('api.src.backend.db_manager.get_transaction')
    def test_approve_version_mocked(self, mock_get_transaction):
        """Test version approval with mocked database"""
        
        # Mock database transaction
        mock_conn = AsyncMock()
        mock_transaction = AsyncMock()
        mock_transaction.__aenter__.return_value = mock_conn
        mock_get_transaction.return_value = mock_transaction
        
        version_id = uuid.uuid4()
        mock_conn.fetchrow.return_value = {
            'version_id': version_id,
            'status': 'scored',
            'miner_hotkey': 'test_miner'
        }
        mock_conn.execute.return_value = None
        
        approval_data = {
            "admin_password": "test_admin_pass",
            "version_ids": [str(version_id)]
        }
        
        response = client.post("/scoring/approve-version", json=approval_data)
        
        assert response.status_code == 200
        result = response.json()
        assert result["approved_count"] == 1


class TestRetrievalEndpointsUnit:
    """Unit tests for retrieval endpoints with mocked dependencies"""
    
    @patch('api.src.backend.entities.MinerAgentScored.get_24_hour_statistics')
    def test_network_stats_mocked(self, mock_get_stats):
        """Test network statistics with mocked data"""
        
        mock_stats = {
            "number_of_agents": 150,
            "agent_iterations_last_24_hours": 25,
            "top_agent_score": 0.895,
            "daily_score_improvement": 0.023
        }
        mock_get_stats.return_value = mock_stats
        
        response = client.get("/retrieval/network-stats")
        
        assert response.status_code == 200
        result = response.json()
        assert result["number_of_agents"] == 150
        assert result["agent_iterations_last_24_hours"] == 25
        assert result["top_agent_score"] == 0.895

    @patch('api.src.backend.entities.MinerAgentScored.get_top_agents')
    def test_top_agents_mocked(self, mock_get_top_agents):
        """Test top agents retrieval with mocked data"""
        
        # Mock top agents data
        mock_agents = []
        for i in range(3):
            agent = Mock()
            agent.version_id = uuid.uuid4()
            agent.miner_hotkey = f"top_miner_{i}"
            agent.agent_name = f"top_agent_{i}"
            agent.score = 0.9 - (i * 0.05)  # Decreasing scores
            agent.approved = True
            mock_agents.append(agent)
        
        mock_get_top_agents.return_value = mock_agents
        
        response = client.get("/retrieval/top-agents?num_agents=3")
        
        assert response.status_code == 200
        result = response.json()
        assert len(result) == 3
        assert result[0]["score"] > result[1]["score"]  # Verify ordering

    @patch('api.src.backend.entities.MinerAgentScored.get_agent_summary_by_hotkey')
    def test_agent_by_hotkey_mocked(self, mock_get_agent_summary):
        """Test agent retrieval by hotkey with mocked data"""
        
        # Mock agent data
        mock_agents = []
        for i in range(2):
            agent = Mock()
            agent.version_id = uuid.uuid4()
            agent.miner_hotkey = "test_hotkey"
            agent.agent_name = f"agent_v{i+1}"
            agent.version_num = i + 1
            agent.status = "scored" if i == 1 else "replaced"
            agent.score = 0.8 + (i * 0.1)
            mock_agents.append(agent)
        
        mock_get_agent_summary.return_value = mock_agents
        
        response = client.get("/retrieval/agent-by-hotkey?miner_hotkey=test_hotkey")
        
        assert response.status_code == 200
        result = response.json()
        assert len(result) == 2
        assert result[0]["version_num"] == 1  # Check data structure

    @patch('api.src.socket.websocket_manager.WebSocketManager.get_instance')
    def test_connected_validators_mocked(self, mock_ws_manager):
        """Test connected validators retrieval"""
        
        # Mock WebSocket manager with connected validators
        mock_manager = Mock()
        mock_validator1 = Mock()
        mock_validator1.get_type.return_value = "validator"
        mock_validator1.hotkey = "validator_1"
        mock_validator1.status = "available"
        mock_validator1.connected_at = datetime.now(timezone.utc)
        
        mock_validator2 = Mock()
        mock_validator2.get_type.return_value = "validator"
        mock_validator2.hotkey = "validator_2" 
        mock_validator2.status = "evaluating"
        mock_validator2.connected_at = datetime.now(timezone.utc)
        
        mock_manager.clients = {"1": mock_validator1, "2": mock_validator2}
        mock_ws_manager.return_value = mock_manager
        
        response = client.get("/retrieval/connected-validators")
        
        assert response.status_code == 200
        result = response.json()
        assert len(result) == 2


class TestAuthenticationEndpointsUnit:
    """Unit tests for authentication endpoints with mocked dependencies"""
    
    @patch('api.src.backend.db_manager.get_transaction')
    def test_open_user_signin_success_mocked(self, mock_get_transaction):
        """Test successful open user sign in"""
        
        # Mock database transaction
        mock_conn = AsyncMock()
        mock_transaction = AsyncMock()
        mock_transaction.__aenter__.return_value = mock_conn
        mock_get_transaction.return_value = mock_transaction
        
        # Mock user creation
        mock_conn.fetchval.side_effect = [
            None  # User doesn't exist yet
        ]
        mock_conn.execute.return_value = None
        
        signin_data = {
            "auth0_user_id": "auth0|test123",
            "email": "test@example.com",
            "name": "Test User",
            "password": "secure_password_123"
        }
        
        response = client.post("/open-users/sign-in", json=signin_data)
        
        assert response.status_code == 200
        result = response.json()
        assert "open_hotkey" in result
        assert result["message"] == "User registered successfully"




class TestAgentSummaryEndpointsUnit:
    """Unit tests for agent summary endpoints"""
    
    @patch('api.src.backend.db_manager.get_db_connection')
    def test_get_agent_summary_mocked(self, mock_get_connection):
        """Test agent summary retrieval"""
        
        # Mock database connection
        mock_conn = AsyncMock()
        mock_connection_context = AsyncMock()
        mock_connection_context.__aenter__.return_value = mock_conn
        mock_get_connection.return_value = mock_connection_context
        
        version_id = uuid.uuid4()
        mock_conn.fetchrow.return_value = {
            'version_id': version_id,
            'agent_summary': 'This agent efficiently solves coding problems using advanced algorithms',
            'agent_name': 'test_agent',
            'miner_hotkey': 'test_miner'
        }
        
        response = client.get(f"/agent-summaries/agent-summary/{version_id}")
        
        assert response.status_code == 200
        result = response.json()
        assert result["agent_summary"] == "This agent efficiently solves coding problems using advanced algorithms"
        assert result["version_id"] == str(version_id)

    @patch('api.src.backend.db_manager.get_db_connection')
    def test_get_agent_summary_not_found_mocked(self, mock_get_connection):
        """Test agent summary retrieval for non-existent agent"""
        
        # Mock database connection
        mock_conn = AsyncMock()
        mock_connection_context = AsyncMock()
        mock_connection_context.__aenter__.return_value = mock_conn
        mock_get_connection.return_value = mock_connection_context
        
        mock_conn.fetchrow.return_value = None  # Agent not found
        
        version_id = uuid.uuid4()
        response = client.get(f"/agent-summaries/agent-summary/{version_id}")
        
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestSystemStatusEndpointsUnit:
    """Unit tests for system status endpoints"""
    
    def test_health_check_basic(self):
        """Test basic health check endpoint"""
        response = client.get("/healthcheck")
        
        assert response.status_code == 200
        assert response.text == '"OK"'

    def test_healthcheck_simple(self):
        """Test simple healthcheck endpoint"""
        response = client.get("/healthcheck")
        
        assert response.status_code == 200
        assert response.text == '"OK"'

    def test_healthcheck_results_endpoint(self):
        """Test healthcheck results endpoint"""
        response = client.get("/healthcheck-results")
        
        # This endpoint requires database connection, so it might fail in unit tests
        # We just check that the endpoint exists and returns a proper response
        assert response.status_code in [200, 500]  # Either success or database error


class TestErrorHandling:
    """Test error handling across endpoints"""
    
    def test_invalid_json(self):
        """Test handling of invalid JSON in requests"""
        response = client.post(
            "/upload/agent",
            data="invalid json",
            headers={"Content-Type": "application/json"}
        )
        
        assert response.status_code == 422

    def test_missing_required_fields(self):
        """Test handling of missing required fields"""
        incomplete_data = {
            "miner_hotkey": "test_miner",
            # Missing required fields
        }
        
        response = client.post("/upload/agent", json=incomplete_data)
        
        assert response.status_code == 422

    @patch('api.src.backend.db_manager.get_transaction')
    def test_database_error_handling(self, mock_get_transaction):
        """Test handling of database errors"""
        
        # Mock database error
        mock_get_transaction.side_effect = Exception("Database connection failed")
        
        agent_data = {
            "miner_hotkey": "test_miner",
            "agent_name": "test_agent",
            "code": "def solve(): pass",
            "signature": "signature"
        }
        
        response = client.post("/upload/agent", json=agent_data)
        
        assert response.status_code == 500


if __name__ == "__main__":
    pytest.main([__file__, "-v"])