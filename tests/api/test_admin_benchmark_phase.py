from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import config
from api.endpoints import admin as admin_endpoint
from api.endpoints.admin import router as admin_router
from api.exception_handlers import register_exception_handlers
from models.benchmark_phase import BenchmarkPhaseSnapshot
from queries.errors import CompetitionAdminConflictError

NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)
PHASE = BenchmarkPhaseSnapshot(
    phase_id=uuid4(),
    set_id=29,
    started_at=NOW,
    started_by="admin",
    start_reason="launch",
    ended_at=None,
    ended_by=None,
    end_reason=None,
    winner_agent_id=None,
    default_winner_agent_id=None,
    pool=[],
)
BASE = "/admin/competitions/29/benchmark-phase"


def _client(monkeypatch, name: str, mock: AsyncMock) -> TestClient:
    monkeypatch.setattr(admin_endpoint, name, mock)
    app = FastAPI()
    app.include_router(admin_router, prefix="/admin")
    register_exception_handlers(app)
    return TestClient(app)


def _auth() -> dict:
    return {"Authorization": f"Bearer {config.COLDKEY_BAN_ADMIN_API_KEY}"}


def test_start_passes_the_actor_and_reason(monkeypatch):
    start = AsyncMock(return_value=PHASE)
    client = _client(monkeypatch, "start_benchmark_phase", start)
    response = client.post(f"{BASE}/start", json={"reason": "launch"}, headers=_auth())
    assert response.status_code == 200, response.text
    assert start.call_args.kwargs == {"set_id": 29, "actor": admin_endpoint.COMPETITION_ADMIN_ACTOR, "reason": "launch"}


def test_conflicts_are_409_with_their_code(monkeypatch):
    start = AsyncMock(side_effect=CompetitionAdminConflictError("benchmark_phase_active"))
    client = _client(monkeypatch, "start_benchmark_phase", start)
    response = client.post(f"{BASE}/start", json={"reason": "again"}, headers=_auth())
    assert response.status_code == 409
    assert response.json() == {"detail": "benchmark_phase_active"}


def test_end_parses_the_optional_winner(monkeypatch):
    end = AsyncMock(return_value=PHASE)
    client = _client(monkeypatch, "end_benchmark_phase", end)
    winner = uuid4()
    assert (
        client.post(f"{BASE}/end", json={"winner_agent_id": str(winner), "reason": "done"}, headers=_auth()).status_code
        == 200
    )
    assert end.call_args.kwargs["winner_agent_id"] == winner
    assert client.post(f"{BASE}/end", json={"reason": "done"}, headers=_auth()).status_code == 200
    assert end.call_args.kwargs["winner_agent_id"] is None
    assert (
        client.post(f"{BASE}/end", json={"winner_agent_id": "nope", "reason": "done"}, headers=_auth()).status_code
        == 422
    )


def test_get_without_a_phase_is_404(monkeypatch):
    client = _client(monkeypatch, "get_benchmark_phase", AsyncMock(return_value=None))
    response = client.get(BASE, headers=_auth())
    assert response.status_code == 404
    assert response.json() == {"detail": "no_benchmark_phase"}


def test_routes_require_admin_auth(monkeypatch):
    client = _client(monkeypatch, "start_benchmark_phase", AsyncMock(return_value=PHASE))
    response = client.post(f"{BASE}/start", json={"reason": "launch"}, headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401
