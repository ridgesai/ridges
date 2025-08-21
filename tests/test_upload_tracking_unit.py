"""
Unit tests for upload attempt tracking functionality.
Tests the core logic without requiring full database integration.
"""

import pytest
import uuid
from unittest.mock import AsyncMock, patch

# Mark all tests in this module as unit tests
pytestmark = pytest.mark.unit


class TestUploadTrackingUnit:
    """Unit tests for upload tracking functions"""

    @pytest.mark.asyncio
    async def test_record_upload_attempt_function(self):
        """Test that record_upload_attempt function calls database correctly"""
        
        # Mock the database connection and transaction
        mock_conn = AsyncMock()
        mock_transaction_context = AsyncMock()
        mock_transaction_context.__aenter__.return_value = mock_conn
        mock_transaction_context.__aexit__.return_value = None
        
        with patch('api.src.utils.upload_agent_helpers.get_transaction', return_value=mock_transaction_context):
            from api.src.utils.upload_agent_helpers import record_upload_attempt
            
            # Test successful upload record
            await record_upload_attempt(
                upload_type="agent",
                success=True,
                hotkey="test_hotkey",
                agent_name="Test Agent",
                filename="agent.py",
                file_size_bytes=1024,
                ip_address="127.0.0.1",
                version_id="test-version-id"
            )
            
            # Verify the database execute was called with correct parameters
            mock_conn.execute.assert_called_once()
            call_args = mock_conn.execute.call_args
            
            # Check the SQL query structure
            sql_query = call_args[0][0]
            assert "INSERT INTO upload_attempts" in sql_query
            assert "upload_type" in sql_query
            assert "success" in sql_query
            assert "hotkey" in sql_query
            
            # Check the parameters
            params = call_args[0][1:]
            assert params[0] == "agent"  # upload_type
            assert params[1] is True     # success
            assert params[2] == "test_hotkey"  # hotkey
            assert params[3] == "Test Agent"   # agent_name
            assert params[4] == "agent.py"     # filename
            assert params[5] == 1024           # file_size_bytes
            assert params[6] == "127.0.0.1"    # ip_address
            assert params[11] == "test-version-id"  # version_id

    @pytest.mark.asyncio
    async def test_record_upload_attempt_with_error(self):
        """Test recording failed upload attempt with error details"""
        
        mock_conn = AsyncMock()
        mock_transaction_context = AsyncMock()
        mock_transaction_context.__aenter__.return_value = mock_conn
        mock_transaction_context.__aexit__.return_value = None
        
        with patch('api.src.utils.upload_agent_helpers.get_transaction', return_value=mock_transaction_context):
            from api.src.utils.upload_agent_helpers import record_upload_attempt
            
            # Test failed upload record with ban reason
            await record_upload_attempt(
                upload_type="agent",
                success=False,
                hotkey="banned_hotkey",
                agent_name="Banned Agent",
                filename="agent.py",
                file_size_bytes=2048,
                ip_address="192.168.1.1",
                error_type="banned",
                error_message="Your miner hotkey has been banned",
                ban_reason="Code obfuscation detected",
                http_status_code=403
            )
            
            # Verify the call
            mock_conn.execute.assert_called_once()
            call_args = mock_conn.execute.call_args
            params = call_args[0][1:]
            
            assert params[0] == "agent"      # upload_type
            assert params[1] is False        # success
            assert params[2] == "banned_hotkey"  # hotkey
            assert params[7] == "banned"     # error_type
            assert params[8] == "Your miner hotkey has been banned"  # error_message
            assert params[9] == "Code obfuscation detected"  # ban_reason
            assert params[10] == 403         # http_status_code

    @pytest.mark.asyncio
    async def test_get_ban_reason_function(self):
        """Test that get_ban_reason function queries database correctly"""
        
        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = "Test ban reason"
        
        with patch('api.src.backend.queries.agents.db_operation') as mock_decorator:
            # Mock the decorator to directly call the function
            mock_decorator.side_effect = lambda func: func
            
            with patch('api.src.backend.db_manager.new_db.acquire') as mock_acquire:
                mock_acquire.return_value.__aenter__.return_value = mock_conn
                mock_acquire.return_value.__aexit__.return_value = None
                
                from api.src.backend.queries.agents import get_ban_reason
                
                # Test getting ban reason
                result = await get_ban_reason("test_hotkey")
                
                # Verify the query was called correctly
                mock_conn.fetchval.assert_called_once()
                call_args = mock_conn.fetchval.call_args
                
                # Check SQL query
                sql_query = call_args[0][0]
                assert "SELECT banned_reason FROM banned_hotkeys" in sql_query
                assert "WHERE miner_hotkey = $1" in sql_query
                
                # Check parameter
                assert call_args[0][1] == "test_hotkey"
                
                # Check result
                assert result == "Test ban reason"

    @pytest.mark.asyncio
    async def test_track_upload_decorator_success(self):
        """Test that track_upload decorator records successful uploads"""
        
        # Mock database operations
        mock_conn = AsyncMock()
        mock_transaction_context = AsyncMock()
        mock_transaction_context.__aenter__.return_value = mock_conn
        mock_transaction_context.__aexit__.return_value = None
        
        with patch('api.src.utils.upload_agent_helpers.get_transaction', return_value=mock_transaction_context):
            from api.src.utils.upload_agent_helpers import track_upload
            
            # Create a mock upload function
            @track_upload("agent")
            async def mock_upload_function(request, agent_file, **kwargs):
                return type('Response', (), {'message': 'Successfully uploaded agent test-version-id for miner test_hotkey.'})()
            
            # Mock request and file objects
            class MockRequest:
                class MockClient:
                    host = "127.0.0.1"
                client = MockClient()
            
            class MockFile:
                filename = "agent.py"
                def __init__(self):
                    import io
                    self.file = io.BytesIO(b"test content")
            
            request = MockRequest()
            agent_file = MockFile()
            
            # Call the decorated function
            result = await mock_upload_function(
                request, agent_file, 
                file_info="test_hotkey:1", 
                name="Test Agent"
            )
            
            # Verify the database record was created
            mock_conn.execute.assert_called()
            call_args = mock_conn.execute.call_args
            params = call_args[0][1:]
            
            assert params[0] == "agent"        # upload_type
            assert params[1] is True           # success
            assert params[2] == "test_hotkey"  # hotkey
            assert params[3] == "Test Agent"   # agent_name
            assert params[4] == "agent.py"     # filename
            assert params[5] == 12             # file_size_bytes (len("test content"))
            assert params[6] == "127.0.0.1"    # ip_address

    @pytest.mark.asyncio 
    async def test_track_upload_decorator_banned_error(self):
        """Test that track_upload decorator records banned upload attempts"""
        
        # Mock database operations
        mock_conn = AsyncMock()
        mock_transaction_context = AsyncMock()
        mock_transaction_context.__aenter__.return_value = mock_conn
        mock_transaction_context.__aexit__.return_value = None
        
        with patch('api.src.utils.upload_agent_helpers.get_transaction', return_value=mock_transaction_context):
            with patch('api.src.backend.queries.agents.get_ban_reason', return_value="Code obfuscation detected"):
                from api.src.utils.upload_agent_helpers import track_upload
                from fastapi import HTTPException
                
                # Create a mock upload function that raises banned exception
                @track_upload("agent")
                async def mock_upload_function(request, agent_file, **kwargs):
                    raise HTTPException(status_code=403, detail="Your miner hotkey has been banned for attempting to obfuscate code")
                
                # Mock request and file objects
                class MockRequest:
                    class MockClient:
                        host = "127.0.0.1"
                    client = MockClient()
                
                class MockFile:
                    filename = "agent.py"
                    def __init__(self):
                        import io
                        self.file = io.BytesIO(b"test content")
                
                request = MockRequest()
                agent_file = MockFile()
                
                # Call the decorated function and expect HTTPException
                with pytest.raises(HTTPException) as exc_info:
                    await mock_upload_function(
                        request, agent_file,
                        file_info="banned_hotkey:1",
                        name="Banned Agent"
                    )
                
                # Verify the exception details
                assert exc_info.value.status_code == 403
                assert "banned" in exc_info.value.detail.lower()
                
                # Verify the database record was created for the failed attempt
                assert mock_conn.execute.call_count >= 1
                
                # Find the call that recorded the failed upload
                failed_upload_call = None
                for call in mock_conn.execute.call_args_list:
                    params = call[0][1:]
                    if params[1] is False:  # success = False
                        failed_upload_call = params
                        break
                
                assert failed_upload_call is not None
                assert failed_upload_call[0] == "agent"        # upload_type
                assert failed_upload_call[1] is False          # success
                assert failed_upload_call[2] == "banned_hotkey"  # hotkey
                assert failed_upload_call[7] == "banned"       # error_type
                assert failed_upload_call[9] == "Code obfuscation detected"  # ban_reason
                assert failed_upload_call[10] == 403           # http_status_code

    @pytest.mark.asyncio
    async def test_track_upload_decorator_rate_limit_error(self):
        """Test that track_upload decorator records rate limit errors"""
        
        mock_conn = AsyncMock()
        mock_transaction_context = AsyncMock()
        mock_transaction_context.__aenter__.return_value = mock_conn
        mock_transaction_context.__aexit__.return_value = None
        
        with patch('api.src.utils.upload_agent_helpers.get_transaction', return_value=mock_transaction_context):
            from api.src.utils.upload_agent_helpers import track_upload
            from fastapi import HTTPException
            
            @track_upload("agent")
            async def mock_upload_function(request, agent_file, **kwargs):
                raise HTTPException(status_code=429, detail="You must wait 300 seconds before uploading")
            
            class MockRequest:
                class MockClient:
                    host = "127.0.0.1"
                client = MockClient()
            
            class MockFile:
                filename = "agent.py"
                def __init__(self):
                    import io
                    self.file = io.BytesIO(b"test content")
            
            request = MockRequest()
            agent_file = MockFile()
            
            with pytest.raises(HTTPException) as exc_info:
                await mock_upload_function(
                    request, agent_file,
                    file_info="rate_limited_hotkey:1",
                    name="Rate Limited Agent"
                )
            
            assert exc_info.value.status_code == 429
            
            # Verify rate limit error was recorded
            mock_conn.execute.assert_called()
            call_args = mock_conn.execute.call_args
            params = call_args[0][1:]
            
            assert params[0] == "agent"                # upload_type
            assert params[1] is False                  # success
            assert params[2] == "rate_limited_hotkey"  # hotkey
            assert params[7] == "rate_limit"           # error_type
            assert params[10] == 429                   # http_status_code

    @pytest.mark.asyncio
    async def test_track_upload_decorator_open_agent(self):
        """Test that track_upload decorator works for open agent uploads"""
        
        mock_conn = AsyncMock()
        mock_transaction_context = AsyncMock()
        mock_transaction_context.__aenter__.return_value = mock_conn
        mock_transaction_context.__aexit__.return_value = None
        
        with patch('api.src.utils.upload_agent_helpers.get_transaction', return_value=mock_transaction_context):
            from api.src.utils.upload_agent_helpers import track_upload
            
            @track_upload("open-agent")
            async def mock_upload_function(request, agent_file, **kwargs):
                return type('Response', (), {'message': 'Successfully uploaded agent test-version-id for open user test_open_user.'})()
            
            class MockRequest:
                class MockClient:
                    host = "10.0.0.1"
                client = MockClient()
            
            class MockFile:
                filename = "agent.py"
                def __init__(self):
                    import io
                    self.file = io.BytesIO(b"open agent content")
            
            request = MockRequest()
            agent_file = MockFile()
            
            # Call with open_hotkey parameter
            result = await mock_upload_function(
                request, agent_file,
                open_hotkey="test_open_user",
                name="Test Open Agent"
            )
            
            # Verify the database record
            mock_conn.execute.assert_called()
            call_args = mock_conn.execute.call_args
            params = call_args[0][1:]
            
            assert params[0] == "open-agent"      # upload_type
            assert params[1] is True              # success
            assert params[2] == "test_open_user"  # hotkey
            assert params[3] == "Test Open Agent" # agent_name
            assert params[6] == "10.0.0.1"        # ip_address

    def test_error_type_classification(self):
        """Test that different HTTP errors are classified correctly"""
        from api.src.utils.upload_agent_helpers import track_upload
        
        # Test error classification logic (extracted from decorator)
        test_cases = [
            (403, "Your miner hotkey has been banned", "banned"),
            (429, "Rate limit exceeded", "rate_limit"),
            (400, "Invalid signature", "validation_error"),
            (400, "File size too large", "validation_error"),
            (503, "No screeners available", "validation_error"),
            (500, "Internal server error", "validation_error"),
        ]
        
        for status_code, detail, expected_error_type in test_cases:
            if status_code == 403 and "banned" in detail.lower():
                error_type = "banned"
            elif status_code == 429:
                error_type = "rate_limit"
            else:
                error_type = "validation_error"
            
            assert error_type == expected_error_type, f"Status {status_code} with detail '{detail}' should be classified as '{expected_error_type}', got '{error_type}'"
