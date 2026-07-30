from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.config as config
from api.endpoints import admin as admin_endpoint
from api.endpoints.admin import router as admin_router


def _fake_flag_queries(monkeypatch, blacklist: list[str]):
    calls: list[tuple] = []

    async def fake_add_hotkey_to_blacklist(hotkey: str) -> list[str]:
        calls.append(("add", hotkey))
        if hotkey not in blacklist:
            blacklist.append(hotkey)
        return blacklist

    async def fake_remove_hotkey_from_blacklist(hotkey: str) -> list[str]:
        calls.append(("remove", hotkey))
        if hotkey in blacklist:
            blacklist.remove(hotkey)
        return blacklist

    async def fake_set_internal_flag(flag, value: str) -> None:
        calls.append(("set", flag, value))

    monkeypatch.setattr(admin_endpoint, "add_hotkey_to_blacklist", fake_add_hotkey_to_blacklist)
    monkeypatch.setattr(admin_endpoint, "remove_hotkey_from_blacklist", fake_remove_hotkey_from_blacklist)
    monkeypatch.setattr(admin_endpoint, "set_internal_flag", fake_set_internal_flag)
    return calls


@pytest.mark.anyio
async def test_put_blacklisted_validator_adds_hotkey(monkeypatch) -> None:
    calls = _fake_flag_queries(monkeypatch, blacklist=["existing-hotkey"])

    response = await admin_endpoint.put_blacklisted_validator("new-hotkey")

    assert calls == [("add", "new-hotkey")]
    assert response.blacklisted_validators == ["existing-hotkey", "new-hotkey"]


@pytest.mark.anyio
async def test_delete_blacklisted_validator_removes_hotkey(monkeypatch) -> None:
    calls = _fake_flag_queries(monkeypatch, blacklist=["hotkey-a", "hotkey-b"])

    response = await admin_endpoint.delete_blacklisted_validator("hotkey-a")

    assert calls == [("remove", "hotkey-a")]
    assert response.blacklisted_validators == ["hotkey-b"]


@pytest.mark.anyio
async def test_put_validators_paused_sets_flag_true(monkeypatch) -> None:
    calls = _fake_flag_queries(monkeypatch, blacklist=[])

    response = await admin_endpoint.put_validators_paused()

    assert calls == [("set", admin_endpoint.InternalFlagName.VALIDATORS_PAUSED, "true")]
    assert response.validators_paused is True


@pytest.mark.anyio
async def test_delete_validators_paused_sets_flag_false(monkeypatch) -> None:
    calls = _fake_flag_queries(monkeypatch, blacklist=[])

    response = await admin_endpoint.delete_validators_paused()

    assert calls == [("set", admin_endpoint.InternalFlagName.VALIDATORS_PAUSED, "false")]
    assert response.validators_paused is False


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(admin_router, prefix="/admin")
    return app


def test_flag_routes_require_admin_bearer_key(monkeypatch) -> None:
    _fake_flag_queries(monkeypatch, blacklist=[])
    client = TestClient(_make_app())
    auth = {"Authorization": f"Bearer {config.COLDKEY_BAN_ADMIN_API_KEY}"}

    routes = [
        ("PUT", "/admin/blacklisted-validators/some-hotkey"),
        ("DELETE", "/admin/blacklisted-validators/some-hotkey"),
        ("PUT", "/admin/validators-paused"),
        ("DELETE", "/admin/validators-paused"),
    ]
    for method, path in routes:
        assert client.request(method, path).status_code == 401, (method, path)
        assert client.request(method, path, headers=auth).status_code == 200, (method, path)
