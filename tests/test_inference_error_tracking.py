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

    def test_reset_inference_errors_clears_count(self):
        for _ in range(5):
            self.ehm.add_inference_error(self.run_id)
        assert self.ehm.get_inference_errors(self.run_id) == 5

        self.ehm.reset_inference_errors(self.run_id)
        assert self.ehm.get_inference_errors(self.run_id) == 0

    def test_reset_inference_errors_noop_for_unknown_run(self):
        # Should not raise
        self.ehm.reset_inference_errors(uuid4())

    def test_reset_does_not_affect_other_runs(self):
        run_a = uuid4()
        run_b = uuid4()
        self.ehm.add_inference_error(run_a)
        self.ehm.add_inference_error(run_a)
        self.ehm.add_inference_error(run_b)

        self.ehm.reset_inference_errors(run_a)
        assert self.ehm.get_inference_errors(run_a) == 0
        assert self.ehm.get_inference_errors(run_b) == 1



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

        async def test_reset_endpoint_clears_errors(self, client):
            """POST /api/reset-inference-errors should clear the error count."""
            run_id = str(uuid4())

            # Add some errors
            from uuid import UUID
            global_error_hash_map.add_inference_error(UUID(run_id))
            global_error_hash_map.add_inference_error(UUID(run_id))
            assert global_error_hash_map.get_inference_errors(UUID(run_id)) == 2

            # Reset via endpoint
            response = await client.post(f"/api/reset-inference-errors?evaluation_run_id={run_id}")
            assert response.status_code == 200

            # Errors should be cleared
            assert global_error_hash_map.get_inference_errors(UUID(run_id)) == 0

        async def test_reset_endpoint_allows_new_inferences_after_threshold(self, client):
            """After resetting errors, the error count is 0 so the threshold check passes."""
            run_id = str(uuid4())
            from uuid import UUID

            with patch("inference_gateway.main.config") as mock_config:
                mock_config.USE_DATABASE = False
                mock_config.MAX_COST_PER_EVALUATION_RUN_USD = 10.0
                mock_config.MAX_INFERENCE_ERRORS_PER_EVALUATION_RUN = 2

                # Fill up the error counter to the threshold
                global_error_hash_map.add_inference_error(UUID(run_id))
                global_error_hash_map.add_inference_error(UUID(run_id))
                assert global_error_hash_map.get_inference_errors(UUID(run_id)) == 2

                # Reset errors via endpoint
                reset_response = await client.post(f"/api/reset-inference-errors?evaluation_run_id={run_id}")
                assert reset_response.status_code == 200
                assert global_error_hash_map.get_inference_errors(UUID(run_id)) == 0

                # Verify usage endpoint also reflects the reset
                usage_response = await client.get(f"/api/usage?evaluation_run_id={run_id}")
                assert usage_response.status_code == 200
                assert usage_response.json()["inference_errors"] == 0



# ---------------------------------------------------------------------------
# Integration test: validator retry logic
#
# This test mocks the sandbox/problem suite to verify that the validator
# retries a single run when inference errors exceed the threshold, rather
# than failing the entire evaluation.
#
# Requires: the full validator environment (sandbox_manager, problem_suites)
# Run with: python3 -m pytest tests/test_inference_error_tracking.py::TestValidatorRetryLogic -v
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not os.getenv("NETUID"),
    reason="Requires full validator environment (.env with NETUID, etc.)"
)
class TestValidatorRetryLogic:
    """Tests for the validator's per-run retry behavior on platform inference errors.

    These tests require the full validator environment to be configured.
    Run with: NETUID=... python3 -m pytest tests/test_inference_error_tracking.py::TestValidatorRetryLogic -v
    """

    @pytest.fixture
    def mock_httpx_responses(self):
        """Create mock httpx responses for the inference gateway usage/reset endpoints."""
        call_count = {"check": 0, "reset": 0}

        class MockResponse:
            def __init__(self, status_code, json_data=None):
                self.status_code = status_code
                self._json = json_data or {}
            def json(self):
                return self._json

        class MockClient:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def get(self, url):
                call_count["check"] += 1
                # First call: errors exceed threshold (triggers retry)
                # Second call: errors below threshold (retry succeeded)
                if call_count["check"] == 1:
                    return MockResponse(200, {"inference_errors": 5, "max_inference_errors": 5})
                return MockResponse(200, {"inference_errors": 0, "max_inference_errors": 5})
            async def post(self, url):
                call_count["reset"] += 1
                return MockResponse(200)

        return MockClient, call_count

    @pytest.mark.asyncio
    async def test_retry_resets_errors_and_reruns(self, mock_httpx_responses):
        """Validator should retry a run when inference errors exceed threshold."""
        MockClient, call_count = mock_httpx_responses
        from unittest.mock import AsyncMock, patch, MagicMock
        from uuid import uuid4
        from models.problem import ProblemTestResultStatus
        from models.evaluation_run import EvaluationRunStatus

        run_id = uuid4()
        problem_name = "test-problem"
        agent_code = "print('hello')"
        run_count = {"value": 0}

        # Mock problem suite
        mock_suite = MagicMock()
        mock_suite.has_problem_name.return_value = True
        mock_suite.get_problem.return_value = MagicMock()
        mock_suite.initialize_agent_sandbox.return_value = MagicMock()
        mock_suite.run_agent_sandbox.return_value = ("patch content", "agent logs")
        mock_suite.initialize_eval_sandbox.return_value = MagicMock()

        mock_test_result = MagicMock()
        mock_test_result.status = ProblemTestResultStatus.PASS
        mock_test_result.model_dump.return_value = {"status": "pass", "name": "test"}
        mock_suite.run_eval_sandbox.return_value = ([mock_test_result], "eval logs")

        # Track how many times the agent sandbox is initialized (= number of attempts)
        original_init = mock_suite.initialize_agent_sandbox
        def counting_init(*args, **kwargs):
            run_count["value"] += 1
            return original_init(*args, **kwargs)
        mock_suite.initialize_agent_sandbox.side_effect = counting_init

        # Import and patch
        import validator.main as val_main

        updates = []
        async def mock_update(eid, pname, status, extra=None):
            updates.append((status, extra))

        with patch.object(val_main, "problem_suites", [mock_suite]), \
             patch.object(val_main, "sandbox_manager", MagicMock()), \
             patch.object(val_main, "running_agent_timeout_seconds", 60), \
             patch.object(val_main, "running_eval_timeout_seconds", 60), \
             patch.object(val_main, "update_evaluation_run", mock_update), \
             patch.object(val_main, "truncate_logs_if_required", lambda x: x), \
             patch("httpx.AsyncClient", MockClient):

            await val_main._run_evaluation_run(run_id, problem_name, agent_code)

        # Should have been called twice (first attempt + one retry)
        assert run_count["value"] == 2, f"Expected 2 attempts, got {run_count['value']}"
        # Should have reset errors once
        assert call_count["reset"] == 1, f"Expected 1 reset call, got {call_count['reset']}"
        # Should have checked errors twice
        assert call_count["check"] == 2, f"Expected 2 check calls, got {call_count['check']}"
        # Final status should be finished (not error)
        final_status = updates[-1][0]
        assert final_status == EvaluationRunStatus.finished, f"Expected finished, got {final_status}"
