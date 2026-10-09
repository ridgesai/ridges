from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.config as config
from api.endpoints import admin
from models.validator_scheduling import ValidatorAllowlistSnapshot
from utils.validator_hotkeys import WHITELISTED_VALIDATORS

HOTKEY = WHITELISTED_VALIDATORS[0]["hotkey"]
ALLOW_PATH = f"/admin/validators/{HOTKEY}/competition-allowlist"
MODE_PATH = "/admin/competitions/29/validator-scheduling"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, "COLDKEY_BAN_ADMIN_API_KEY", "scheduling-test")
    app = FastAPI()
    app.include_router(admin.router, prefix="/admin")
    return TestClient(app)


HEADERS = {"Authorization": "Bearer scheduling-test"}


@pytest.mark.parametrize(
    "path,body",
    [(MODE_PATH, {"mode": "disabled", "reason": "test"}), (ALLOW_PATH, {"allowed_set_ids": [29], "reason": "test"})],
)
def test_all_controls_require_admin_auth(client, path, body):
    assert client.get(path).status_code == 401
    assert client.put(path, json=body).status_code == 401


@pytest.mark.parametrize("ids", [[True], ["29"], [-1], [2147483648], [29, 29], "29"])
def test_rejects_invalid_allowlists(client, ids):
    assert client.put(ALLOW_PATH, json={"allowed_set_ids": ids, "reason": "test"}, headers=HEADERS).status_code == 422


@pytest.mark.parametrize("ids", [None, [], [29], [28, 29]])
def test_allowlist_admin_contract(client, monkeypatch, ids):
    save = AsyncMock(return_value=ValidatorAllowlistSnapshot(validator_hotkey=HOTKEY, allowed_set_ids=ids))
    monkeypatch.setattr(admin, "set_validator_allowlist", save)
    response = client.put(ALLOW_PATH, json={"allowed_set_ids": ids, "reason": "test"}, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["allowed_set_ids"] == ids
    assert save.await_args.kwargs["actor"] == admin.COMPETITION_ADMIN_ACTOR


@pytest.mark.parametrize(
    "body",
    [
        {"mode": "bad", "reason": "test"},
        {"mode": "disabled", "reason": " "},
        {"mode": "normal", "reason": "test", "extra": 1},
    ],
)
def test_rejects_invalid_mode_payloads(client, body):
    assert client.put(MODE_PATH, json=body, headers=HEADERS).status_code == 422


def test_allowlist_requires_reason_and_valid_whitelisted_hotkey(client, monkeypatch):
    assert client.put(ALLOW_PATH, json={"allowed_set_ids": [29]}, headers=HEADERS).status_code == 422
    assert (
        client.put(
            "/admin/validators/invalid/competition-allowlist",
            json={"allowed_set_ids": [29], "reason": "test"},
            headers=HEADERS,
        ).status_code
        == 400
    )
    monkeypatch.setattr(admin, "is_validator_hotkey_whitelisted", lambda _: False)
    assert client.put(ALLOW_PATH, json={"allowed_set_ids": [29], "reason": "test"}, headers=HEADERS).status_code == 400
    save = AsyncMock(return_value=ValidatorAllowlistSnapshot(validator_hotkey=HOTKEY, allowed_set_ids=None))
    monkeypatch.setattr(admin, "set_validator_allowlist", save)
    assert (
        client.put(
            ALLOW_PATH, json={"allowed_set_ids": None, "reason": "remove stale validator"}, headers=HEADERS
        ).status_code
        == 200
    )


@pytest.mark.parametrize("mode", ["disabled", "normal", "prioritized"])
def test_scheduling_read_and_write_contract(client, monkeypatch, mode):
    from models.validator_scheduling import CompetitionSchedulingSnapshot

    snapshot = CompetitionSchedulingSnapshot(set_id=29, mode=mode)
    save = AsyncMock(return_value=snapshot)
    monkeypatch.setattr(admin, "set_competition_scheduling", save)
    monkeypatch.setattr(admin, "get_competition_scheduling", AsyncMock(return_value=snapshot))
    response = client.put(MODE_PATH, json={"mode": mode, "reason": "operator change"}, headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {"set_id": 29, "mode": mode}
    assert save.await_args.kwargs["actor"] == admin.COMPETITION_ADMIN_ACTOR
    assert client.get(MODE_PATH, headers=HEADERS).json() == response.json()


def test_allowlist_get_preserves_null_vs_empty(client, monkeypatch):
    for ids in (None, [], [29]):
        monkeypatch.setattr(
            admin,
            "get_validator_allowlist",
            AsyncMock(return_value=ValidatorAllowlistSnapshot(validator_hotkey=HOTKEY, allowed_set_ids=ids)),
        )
        response = client.get(ALLOW_PATH, headers=HEADERS)
        assert response.status_code == 200
        assert response.json()["allowed_set_ids"] == ids
