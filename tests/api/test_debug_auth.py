from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.config as config
from api.endpoints import debug


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, "COLDKEY_BAN_ADMIN_API_KEY", "debug-test-admin-key")
    app = FastAPI()
    app.include_router(debug.router, prefix="/debug")
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("route", ["lock-info", "query-info"])
@pytest.mark.parametrize("authorization", [None, "Bearer wrong-key", "Basic wrong-key", "Bearer"])
def test_debug_rejects_unauthorized_requests_before_reading_data(client, monkeypatch, route, authorization):
    reader = Mock()
    monkeypatch.setattr(debug, "get_debug_" + route.replace("-", "_"), reader)

    headers = {} if authorization is None else {"Authorization": authorization}
    response = client.get(f"/debug/{route}", headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    reader.assert_not_called()


@pytest.mark.parametrize("route", ["lock-info", "query-info"])
def test_debug_allows_admin(client, monkeypatch, route):
    reader = Mock(return_value={"private-debug-data": ["test entry"]})
    monkeypatch.setattr(debug, "get_debug_" + route.replace("-", "_"), reader)

    response = client.get(f"/debug/{route}", headers={"Authorization": "Bearer debug-test-admin-key"})

    assert response.status_code == 200
    assert response.json() == {"private-debug-data": ["test entry"]}
    reader.assert_called_once_with()


@pytest.mark.parametrize("route", ["lock-info", "query-info"])
def test_debug_fails_closed_without_admin_key(client, monkeypatch, route):
    monkeypatch.setattr(config, "COLDKEY_BAN_ADMIN_API_KEY", "")
    reader = Mock()
    monkeypatch.setattr(debug, "get_debug_" + route.replace("-", "_"), reader)

    response = client.get(f"/debug/{route}", headers={"Authorization": "Bearer debug-test-admin-key"})

    assert response.status_code == 503
    reader.assert_not_called()


def test_debug_openapi_declares_bearer_auth(client):
    paths = client.get("/openapi.json").json()["paths"]
    for route in ("lock-info", "query-info"):
        assert paths[f"/debug/{route}"]["get"]["security"] == [{"HTTPBearer": []}]
