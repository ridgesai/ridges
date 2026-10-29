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
    [
        (MODE_PATH, {"mode": "disabled", "reason": "test"}),
        (ALLOW_PATH, {"allowed_set_ids": [29], "reason": "test"}),
        (
            "/admin/validators/platform-validator-1/competition-allowlist",
            {"allowed_set_ids": [29], "reason": "test"},
        ),
    ],
)
def test_all_controls_require_admin_auth(client, path, body):
    assert client.get(path).status_code == 401
    assert client.put(path, json=body).status_code == 401


@pytest.mark.parametrize("ids", [[True], ["29"], [-1], [2147483648], [29, 29], "29"])
def test_rejects_invalid_allowlists(client, ids):
    assert client.put(ALLOW_PATH, json={"allowed_set_ids": ids, "reason": "test"}, headers=HEADERS).status_code == 422


@pytest.mark.parametrize("ids", [None, [], [29], [28, 29]])
@pytest.mark.parametrize("hotkey", [HOTKEY, "platform-validator-1"])
def test_allowlist_admin_contract(client, monkeypatch, ids, hotkey):
    save = AsyncMock(return_value=ValidatorAllowlistSnapshot(validator_hotkey=hotkey, allowed_set_ids=ids))
    monkeypatch.setattr(admin, "set_validator_allowlist", save)
    path = f"/admin/validators/{hotkey}/competition-allowlist"
    response = client.put(path, json={"allowed_set_ids": ids, "reason": "test"}, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["allowed_set_ids"] == ids
    assert save.await_args.kwargs["validator_hotkey"] == hotkey
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


@pytest.mark.parametrize("hotkey", [HOTKEY, "platform-validator-1"])
def test_allowlist_get_preserves_null_vs_empty(client, monkeypatch, hotkey):
    for ids in (None, [], [29]):
        monkeypatch.setattr(
            admin,
            "get_validator_allowlist",
            AsyncMock(return_value=ValidatorAllowlistSnapshot(validator_hotkey=hotkey, allowed_set_ids=ids)),
        )
        response = client.get(f"/admin/validators/{hotkey}/competition-allowlist", headers=HEADERS)
        assert response.status_code == 200
        assert response.json()["allowed_set_ids"] == ids


@pytest.mark.parametrize(
    "hotkey",
    ["platform-validator-", "platform-validator-1x", "platform-validator-1-extra", "not-platform-validator-1"],
)
def test_all_validator_controls_reject_malformed_platform_names(client, hotkey):
    allowlist_path = f"/admin/validators/{hotkey}/competition-allowlist"
    concurrency_path = f"/admin/competitions/29/validator-concurrency/{hotkey}"
    for method, path, body in [
        ("GET", allowlist_path, None),
        ("PUT", allowlist_path, {"allowed_set_ids": [29], "reason": "test"}),
        ("PUT", concurrency_path, {"max_concurrent_evaluation_runs": 10, "reason": "test"}),
        ("DELETE", concurrency_path, {"reason": "test"}),
    ]:
        assert client.request(method, path, json=body, headers=HEADERS).status_code == 400


def test_platform_validator_name_is_not_accepted_as_a_miner_hotkey(client):
    response = client.post(
        "/admin/upload-credits",
        json={"miner_hotkey": "platform-validator-1", "reason": "test", "granted_by": "test"},
        headers=HEADERS,
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid hotkey SS58 address"
