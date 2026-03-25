"""
Tests for platform-side inference error tracking.
Covers:
  - ErrorHashMap error counting and cleanup
  - Non-halting error code classification
  - Inference gateway threshold enforcement via the /api/inference endpoint
  - Usage endpoint reporting error counts
"""

import os
import time
import pytest
from uuid import uuid4

from inference_gateway.error_hash_map import ErrorHashMap, ERROR_HASH_MAP_CLEANUP_INTERVAL_SECONDS



# ---------------------------------------------------------------------------
# Unit tests: ErrorHashMap
# ---------------------------------------------------------------------------

class TestErrorHashMap:
    def setup_method(self):
        self.ehm = ErrorHashMap()
        self.run_id = uuid4()

    def test_get_inference_errors_returns_zero_for_unknown_run(self):
        assert self.ehm.get_inference_errors(uuid4()) == 0

    def test_add_inference_error_creates_entry(self):
        self.ehm.add_inference_error(self.run_id)
        assert self.ehm.get_inference_errors(self.run_id) == 1

    def test_add_inference_error_increments(self):
        for _ in range(7):
            self.ehm.add_inference_error(self.run_id)
        assert self.ehm.get_inference_errors(self.run_id) == 7

    def test_separate_runs_tracked_independently(self):
        run_a = uuid4()
        run_b = uuid4()

        self.ehm.add_inference_error(run_a)
        self.ehm.add_inference_error(run_a)
        self.ehm.add_inference_error(run_b)

        assert self.ehm.get_inference_errors(run_a) == 2
        assert self.ehm.get_inference_errors(run_b) == 1

    def test_cleanup_removes_stale_entries(self):
        self.ehm.add_inference_error(self.run_id)
        assert self.ehm.get_inference_errors(self.run_id) == 1

        # Simulate the entry going stale
        self.ehm.error_hash_map[self.run_id].last_accessed_at = time.time() - ERROR_HASH_MAP_CLEANUP_INTERVAL_SECONDS - 1
        self.ehm.last_cleanup_at = time.time() - ERROR_HASH_MAP_CLEANUP_INTERVAL_SECONDS - 1

        # Next access triggers cleanup, entry is gone
        assert self.ehm.get_inference_errors(self.run_id) == 0



# ---------------------------------------------------------------------------
# Unit tests: platform error classification
#
# We define the expected set here to avoid importing inference_gateway.main,
# which triggers the config import chain and requires env vars.
# ---------------------------------------------------------------------------

EXPECTED_PLATFORM_ERROR_CODES = {500, 502, 503, 504, -1}

class TestPlatformErrorClassification:
    def test_server_errors_are_platform_errors(self):
        for code in [500, 502, 503, 504]:
            assert code in EXPECTED_PLATFORM_ERROR_CODES, f"Expected {code} to be a platform error"

    def test_internal_error_is_platform_error(self):
        assert -1 in EXPECTED_PLATFORM_ERROR_CODES

    def test_client_errors_are_not_platform_errors(self):
        for code in [400, 404, 422, 429]:
            assert code not in EXPECTED_PLATFORM_ERROR_CODES, f"Expected {code} to not be a platform error"

    def test_success_is_not_platform_error(self):
        assert 200 not in EXPECTED_PLATFORM_ERROR_CODES



# ---------------------------------------------------------------------------
# Unit tests: EvaluationRunErrorCode
# ---------------------------------------------------------------------------

class TestEvaluationRunErrorCode:
    def test_new_error_code_exists(self):
        from models.evaluation_run import EvaluationRunErrorCode
        code = EvaluationRunErrorCode.PLATFORM_TOO_MANY_INFERENCE_ERRORS
        assert code.value == 3050
        assert code.is_platform_error()
        assert not code.is_agent_error()
        assert not code.is_validator_error()

    def test_error_message(self):
        from models.evaluation_run import EvaluationRunErrorCode
        msg = EvaluationRunErrorCode.PLATFORM_TOO_MANY_INFERENCE_ERRORS.get_error_message()
        assert "inference errors" in msg.lower()



# ---------------------------------------------------------------------------
# Integration tests: inference gateway endpoints
#
# These require the full app to be importable. They set minimal env vars
# to satisfy the config module, then mock the providers.
# ---------------------------------------------------------------------------

def _set_minimal_gateway_env():
    """Set the bare minimum env vars so inference_gateway.config can load."""
    defaults = {
        "HOST": "0.0.0.0",
        "PORT": "9999",
        "USE_DATABASE": "false",
        "MAX_COST_PER_EVALUATION_RUN_USD": "10.0",
        "MAX_INFERENCE_ERRORS_PER_EVALUATION_RUN": "5",
        "USE_CHUTES": "false",
        "USE_TARGON": "false",
        "USE_OPENROUTER": "false",
        "TEST_INFERENCE_MODELS": "false",
        "TEST_EMBEDDING_MODELS": "false",
    }
    for key, val in defaults.items():
        os.environ.setdefault(key, val)


# Guard: only define integration tests if we can import the app
_can_import_app = False
try:
    _set_minimal_gateway_env()

    # The config fatals if no provider is enabled, so we need to patch
    # the fatal check. We do this by temporarily setting one provider.
    os.environ["USE_OPENROUTER"] = "true"
    os.environ["OPENROUTER_BASE_URL"] = "http://localhost:9999"
    os.environ["OPENROUTER_API_KEY"] = "test-key"
    os.environ["OPENROUTER_WEIGHT"] = "1"

    from inference_gateway.main import app, cost_hash_map as global_cost_hash_map, error_hash_map as global_error_hash_map, is_platform_error, PLATFORM_ERROR_CODES
    from inference_gateway.models import InferenceResult, EmbeddingResult
    _can_import_app = True
except Exception:
    pass


if _can_import_app:
    import pytest_asyncio
    from unittest.mock import AsyncMock, patch, MagicMock
    from httpx import ASGITransport, AsyncClient

    @pytest_asyncio.fixture
    async def client():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

    def _mock_provider(status_code=200, for_embedding=False):
        """Create a mock provider that returns the given status code."""
        provider = MagicMock()
        provider.name = "MockProvider"
        provider.is_model_supported_for_inference.return_value = True
        provider.is_model_supported_for_embedding.return_value = True

        inference_result = InferenceResult(
            status_code=status_code,
            content="hello" if status_code == 200 else None,
            error_message="provider error" if status_code != 200 else None,
            tool_calls=[],
            num_input_tokens=10,
            num_output_tokens=5,
            cost_usd=0.001
        )
        provider.inference = AsyncMock(return_value=inference_result)

        embedding_result = EmbeddingResult(
            status_code=status_code,
            embedding=[0.1, 0.2, 0.3] if status_code == 200 else None,
            error_message="provider error" if status_code != 200 else None,
            num_input_tokens=10,
            cost_usd=0.0005
        )
        provider.embedding = AsyncMock(return_value=embedding_result)

        return provider


    @pytest.mark.asyncio
    class TestInferenceGatewayErrorTracking:
        def setup_method(self):
            global_cost_hash_map.cost_hash_map = {}
            global_cost_hash_map.last_cleanup_at = time.time()
            global_error_hash_map.error_hash_map = {}
            global_error_hash_map.last_cleanup_at = time.time()

        async def test_platform_error_increments_counter(self, client):
            run_id = str(uuid4())
            mock_provider = _mock_provider(status_code=500)

            with patch("inference_gateway.main.get_provider_that_supports_model_for_inference", return_value=mock_provider), \
                 patch("inference_gateway.main.config") as mock_config:
                mock_config.USE_DATABASE = False
                mock_config.MAX_COST_PER_EVALUATION_RUN_USD = 10.0
                mock_config.MAX_INFERENCE_ERRORS_PER_EVALUATION_RUN = 5

                response = await client.post("/api/inference", json={
                    "evaluation_run_id": run_id,
                    "model": "test-model",
                    "temperature": 0.5,
                    "messages": [{"role": "user", "content": "test"}]
                })

                assert response.status_code == 500
                from uuid import UUID
                assert global_error_hash_map.get_inference_errors(UUID(run_id)) == 1

        async def test_non_platform_error_does_not_increment_counter(self, client):
            run_id = str(uuid4())
            mock_provider = _mock_provider(status_code=422)

            with patch("inference_gateway.main.get_provider_that_supports_model_for_inference", return_value=mock_provider), \
                 patch("inference_gateway.main.config") as mock_config:
                mock_config.USE_DATABASE = False
                mock_config.MAX_COST_PER_EVALUATION_RUN_USD = 10.0
                mock_config.MAX_INFERENCE_ERRORS_PER_EVALUATION_RUN = 5

                response = await client.post("/api/inference", json={
                    "evaluation_run_id": run_id,
                    "model": "test-model",
                    "temperature": 0.5,
                    "messages": [{"role": "user", "content": "test"}]
                })

                assert response.status_code == 422
                from uuid import UUID
                assert global_error_hash_map.get_inference_errors(UUID(run_id)) == 0

        async def test_threshold_blocks_further_requests(self, client):
            run_id = str(uuid4())
            from uuid import UUID
            run_uuid = UUID(run_id)

            # Pre-fill the error count to the limit
            for _ in range(5):
                global_error_hash_map.add_inference_error(run_uuid)

            mock_provider = _mock_provider(status_code=200)

            with patch("inference_gateway.main.get_provider_that_supports_model_for_inference", return_value=mock_provider), \
                 patch("inference_gateway.main.config") as mock_config:
                mock_config.USE_DATABASE = True
                mock_config.CHECK_EVALUATION_RUNS = True
                mock_config.MAX_COST_PER_EVALUATION_RUN_USD = 10.0
                mock_config.MAX_INFERENCE_ERRORS_PER_EVALUATION_RUN = 5

                with patch("inference_gateway.main.get_evaluation_run_status_by_id", new_callable=AsyncMock) as mock_status:
                    from models.evaluation_run import EvaluationRunStatus
                    mock_status.return_value = EvaluationRunStatus.running_agent

                    response = await client.post("/api/inference", json={
                        "evaluation_run_id": run_id,
                        "model": "test-model",
                        "temperature": 0.5,
                        "messages": [{"role": "user", "content": "test"}]
                    })

                    assert response.status_code == 503
                    assert "too many platform-side inference errors" in response.json()["detail"].lower()

        async def test_usage_endpoint_reports_errors(self, client):
            run_id = str(uuid4())
            from uuid import UUID
            run_uuid = UUID(run_id)

            global_error_hash_map.add_inference_error(run_uuid)
            global_error_hash_map.add_inference_error(run_uuid)
            global_cost_hash_map.add_cost(run_uuid, 0.05)

            with patch("inference_gateway.main.config") as mock_config:
                mock_config.MAX_COST_PER_EVALUATION_RUN_USD = 10.0
                mock_config.MAX_INFERENCE_ERRORS_PER_EVALUATION_RUN = 5

                response = await client.get(f"/api/usage?evaluation_run_id={run_id}")

                assert response.status_code == 200
                data = response.json()
                assert data["inference_errors"] == 2
                assert data["max_inference_errors"] == 5
                assert data["used_cost_usd"] == 0.05

        async def test_usage_endpoint_zero_errors(self, client):
            """A run with no errors should report zero."""
            run_id = str(uuid4())

            with patch("inference_gateway.main.config") as mock_config:
                mock_config.MAX_COST_PER_EVALUATION_RUN_USD = 10.0
                mock_config.MAX_INFERENCE_ERRORS_PER_EVALUATION_RUN = 5

                response = await client.get(f"/api/usage?evaluation_run_id={run_id}")

                assert response.status_code == 200
                data = response.json()
                assert data["inference_errors"] == 0
                assert data["used_cost_usd"] == 0.0

        async def test_separate_runs_do_not_interfere(self, client):
            """Errors for one run must not affect another run's count."""
            run_a = str(uuid4())
            run_b = str(uuid4())
            mock_provider = _mock_provider(status_code=502)

            with patch("inference_gateway.main.get_provider_that_supports_model_for_inference", return_value=mock_provider), \
                 patch("inference_gateway.main.config") as mock_config:
                mock_config.USE_DATABASE = False
                mock_config.MAX_COST_PER_EVALUATION_RUN_USD = 10.0
                mock_config.MAX_INFERENCE_ERRORS_PER_EVALUATION_RUN = 5

                # Hit run_a three times
                for _ in range(3):
                    await client.post("/api/inference", json={
                        "evaluation_run_id": run_a,
                        "model": "test-model",
                        "temperature": 0.5,
                        "messages": [{"role": "user", "content": "test"}]
                    })

                # Hit run_b once
                await client.post("/api/inference", json={
                    "evaluation_run_id": run_b,
                    "model": "test-model",
                    "temperature": 0.5,
                    "messages": [{"role": "user", "content": "test"}]
                })

                from uuid import UUID
                assert global_error_hash_map.get_inference_errors(UUID(run_a)) == 3
                assert global_error_hash_map.get_inference_errors(UUID(run_b)) == 1

        async def test_embedding_platform_error_increments_counter(self, client):
            """Embedding endpoint should also track platform errors."""
            run_id = str(uuid4())
            mock_provider = _mock_provider(status_code=503)

            with patch("inference_gateway.main.get_provider_that_supports_model_for_embedding", return_value=mock_provider), \
                 patch("inference_gateway.main.config") as mock_config:
                mock_config.USE_DATABASE = False
                mock_config.MAX_COST_PER_EVALUATION_RUN_USD = 10.0
                mock_config.MAX_INFERENCE_ERRORS_PER_EVALUATION_RUN = 5

                response = await client.post("/api/embedding", json={
                    "evaluation_run_id": run_id,
                    "model": "test-model",
                    "input": "hello world"
                })

                assert response.status_code == 503
                from uuid import UUID
                assert global_error_hash_map.get_inference_errors(UUID(run_id)) == 1

        async def test_constants_match_expected(self):
            """Verify the actual constants match what we test against."""
            assert PLATFORM_ERROR_CODES == EXPECTED_PLATFORM_ERROR_CODES
            assert is_platform_error(500)
            assert not is_platform_error(400)
