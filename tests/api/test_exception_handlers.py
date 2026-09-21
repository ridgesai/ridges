from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.exception_handlers import register_exception_handlers
from utils.bittensor import SubtensorUnavailableError


def _client(app: FastAPI) -> TestClient:
    register_exception_handlers(app)
    return TestClient(app, raise_server_exceptions=False)


def test_chain_unavailable_is_a_503_on_any_route() -> None:
    app = FastAPI()

    @app.get("/needs-chain")
    async def needs_chain() -> None:
        raise SubtensorUnavailableError("Subtensor call get_hotkey_owner timed out after 30s")

    response = _client(app).get("/needs-chain")

    assert response.status_code == 503
    assert "retry" in response.json()["detail"].lower()
