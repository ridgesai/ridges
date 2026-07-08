from __future__ import annotations

import asyncio
import base64
import importlib
import io
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException, UploadFile

from api.endpoints import validator as validator_endpoint
from api.endpoints.validator_models import ValidatorRequestEvaluationRequest
from api.src.utils import openrouter_validation as openrouter_validation_module
from api.src.utils.openrouter_validation import OPENROUTER_API_BASE_URL, ValidatedOpenRouterKeys
from db.models import InternalFlagName
from models.agent import Agent, AgentStatus
from models.evaluation import Evaluation
from models.evaluation_run import EvaluationRun, EvaluationRunStatus
from models.evaluation_set import EvaluationSetGroup
from queries.agent import AgentOpenRouterSecrets
from utils.agent_secrets import AgentKeyDecryptError, AgentKeyEncryptionConfigError, decrypt_agent_secret


def _encoded_key() -> str:
    return base64.b64encode(b"k" * 32).decode("ascii")


def _make_request(ip_address: str = "127.0.0.1") -> SimpleNamespace:
    return SimpleNamespace(client=SimpleNamespace(host=ip_address))


def _make_upload_file(content: str = "print('hi')\n") -> UploadFile:
    return UploadFile(filename="agent.py", file=io.BytesIO(content.encode("utf-8")))


def _make_agent(agent_id) -> Agent:
    return Agent(
        agent_id=agent_id,
        miner_hotkey="miner-hotkey",
        name="Agent",
        version_num=0,
        status=AgentStatus.screening_1,
        created_at=datetime.now(timezone.utc),
        ip_address="127.0.0.1",
    )


def _make_evaluation(agent_id, validator_hotkey: str) -> Evaluation:
    return Evaluation(
        evaluation_id=uuid4(),
        agent_id=agent_id,
        validator_hotkey=validator_hotkey,
        set_id=1,
        evaluation_set_group=EvaluationSetGroup.from_validator_hotkey(validator_hotkey),
        created_at=datetime.now(timezone.utc),
    )


def _make_evaluation_run(evaluation_id, *, problem_name: str = "problem-1") -> EvaluationRun:
    return EvaluationRun(
        evaluation_run_id=uuid4(),
        evaluation_id=evaluation_id,
        problem_name=problem_name,
        status=EvaluationRunStatus.pending,
        created_at=datetime.now(timezone.utc),
    )


def _validated_keys() -> ValidatedOpenRouterKeys:
    return ValidatedOpenRouterKeys(
        runtime_api_key="sk-or-v1-runtime",
        management_api_key="sk-or-v1-management",
        workspace_id="workspace-1",
        api_key_label="sk-or-v1-run...ime",
        api_key_creator_user_id="user_123",
        validated_at=datetime.now(timezone.utc),
    )


def test_agent_model_does_not_expose_openrouter_api_keys() -> None:
    assert "openrouter_api_key" not in Agent.model_fields
    assert "openrouter_management_key" not in Agent.model_fields


def _load_upload_endpoint(monkeypatch):
    class FakeSubtensor:
        def __init__(self, *args, **kwargs) -> None:
            self.substrate = SimpleNamespace()

        def get_hotkey_owner(self, *args, **kwargs) -> str:
            return "coldkey"

        def get_balance(self, *args, **kwargs) -> SimpleNamespace:
            return SimpleNamespace(rao=10**12)

    import bittensor

    monkeypatch.setattr(bittensor, "Subtensor", FakeSubtensor)
    sys.modules.pop("api.src.endpoints.upload", None)
    return importlib.import_module("api.src.endpoints.upload")


def _patch_upload_dependencies(
    monkeypatch, *, create_agent_impl, validated_keys: ValidatedOpenRouterKeys | None = None
):
    upload_endpoint = _load_upload_endpoint(monkeypatch)
    monkeypatch.setenv("RIDGES_AGENT_KEY_ENCRYPTION_KEY", _encoded_key())
    monkeypatch.setattr(upload_endpoint.config, "ENV", "dev")
    monkeypatch.setattr(upload_endpoint.config, "PRE_SCREENING_JUDGE_ENABLED", False)
    monkeypatch.setattr(upload_endpoint, "get_miner_hotkey", lambda *_args, **_kwargs: "miner-hotkey")
    monkeypatch.setattr(upload_endpoint, "check_if_python_file", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(upload_endpoint, "check_signature", lambda *_args, **_kwargs: None)

    async def _fake_get_hotkey_owner(*args, **kwargs):
        return "coldkey"

    async def _fake_get_balance(*args, **kwargs):
        return SimpleNamespace(rao=10**12)

    async def _fake_get_alpha_stake(*args, **kwargs):
        return SimpleNamespace(rao=10**12)

    monkeypatch.setattr(
        upload_endpoint,
        "subtensor_client",
        SimpleNamespace(
            get_hotkey_owner=_fake_get_hotkey_owner,
            get_balance=_fake_get_balance,
            get_alpha_stake=_fake_get_alpha_stake,
        ),
    )

    async def fake_validate_openrouter_keys(*, openrouter_api_key: str | None, openrouter_management_key: str | None):
        assert openrouter_api_key is not None
        assert openrouter_management_key is not None
        return validated_keys or _validated_keys()

    async def fake_get_hotkey_lock(_hotkey: str) -> asyncio.Lock:
        return asyncio.Lock()

    async def fake_latest_agent(*, miner_hotkey: str):
        return None

    async def fake_latest_created(*, miner_hotkey: str):
        return None

    async def fake_record_upload_attempt(*_args, **_kwargs) -> None:
        return None

    async def fake_check_hotkey_registered(_hotkey: str) -> None:
        return None

    async def fake_check_agent_banned(*, miner_hotkey: str) -> None:
        return None

    async def fake_get_upload_price(*args, **kwargs):
        return SimpleNamespace(amount_alpha_rao=1)

    async def fake_create_payment_quote(*, miner_hotkey: str, amount_alpha_rao: int, expires_at):
        return SimpleNamespace(
            quote_id=uuid4(),
            miner_hotkey=miner_hotkey,
            amount_alpha_rao=amount_alpha_rao,
            expires_at=expires_at,
        )

    monkeypatch.setattr(upload_endpoint, "validate_openrouter_keys", fake_validate_openrouter_keys)
    monkeypatch.setattr(upload_endpoint, "get_hotkey_lock", fake_get_hotkey_lock)
    monkeypatch.setattr(upload_endpoint, "get_latest_agent_for_miner_hotkey", fake_latest_agent)
    monkeypatch.setattr(
        upload_endpoint,
        "get_latest_agent_created_at_for_miner_hotkey_in_latest_set_id",
        fake_latest_created,
    )
    monkeypatch.setattr(upload_endpoint, "record_upload_attempt", fake_record_upload_attempt)
    monkeypatch.setattr(upload_endpoint, "check_hotkey_registered", fake_check_hotkey_registered)
    monkeypatch.setattr(upload_endpoint, "check_agent_banned", fake_check_agent_banned)
    monkeypatch.setattr(upload_endpoint, "get_upload_price", fake_get_upload_price)
    monkeypatch.setattr(upload_endpoint, "create_payment_quote", fake_create_payment_quote)
    monkeypatch.setattr(upload_endpoint, "create_agent", create_agent_impl)
    return upload_endpoint


def _fake_async_client_factory(routes: dict[tuple[str, tuple[tuple[str, str], ...]], httpx.Response | Exception]):
    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url: str, *, headers=None, params=None):
            key = (url, tuple(sorted((params or {}).items())))
            result = routes.get(key)
            if result is None:
                raise AssertionError(f"Unexpected OpenRouter request: {url} params={params}")
            if isinstance(result, Exception):
                raise result
            return result

    return FakeAsyncClient


@pytest.mark.anyio
async def test_validate_openrouter_keys_succeeds_for_non_default_workspace(monkeypatch) -> None:
    runtime_key = "sk-or-v1-runtime"
    management_key = "sk-or-v1-management"
    routes = {
        (f"{OPENROUTER_API_BASE_URL}/key", ()): httpx.Response(
            200,
            json={"data": {"label": "sk-or-v1-run...ime", "creator_user_id": "user_123"}},
        ),
        (f"{OPENROUTER_API_BASE_URL}/workspaces", ()): httpx.Response(
            200,
            json={"data": [{"id": "default-workspace"}, {"id": "workspace-2"}]},
        ),
        (f"{OPENROUTER_API_BASE_URL}/keys", (("workspace_id", "default-workspace"),)): httpx.Response(
            200,
            json={"data": []},
        ),
        (f"{OPENROUTER_API_BASE_URL}/keys", (("workspace_id", "workspace-2"),)): httpx.Response(
            200,
            json={
                "data": [
                    {
                        "label": "sk-or-v1-run...ime",
                        "creator_user_id": "user_123",
                    }
                ]
            },
        ),
        (f"{OPENROUTER_API_BASE_URL}/workspaces/workspace-2", ()): httpx.Response(
            200,
            json={
                "data": {
                    "is_observability_io_logging_enabled": False,
                    "is_observability_broadcast_enabled": False,
                    "is_data_discount_logging_enabled": False,
                }
            },
        ),
    }
    monkeypatch.setattr(
        openrouter_validation_module.httpx,
        "AsyncClient",
        _fake_async_client_factory(routes),
    )

    validated = await openrouter_validation_module.validate_openrouter_keys(
        openrouter_api_key=f"  {runtime_key}  ",
        openrouter_management_key=f"  {management_key}  ",
    )

    assert validated.runtime_api_key == runtime_key
    assert validated.management_api_key == management_key
    assert validated.workspace_id == "workspace-2"
    assert validated.api_key_label == "sk-or-v1-run...ime"
    assert validated.api_key_creator_user_id == "user_123"


@pytest.mark.anyio
async def test_validate_openrouter_keys_rejects_invalid_management_key(monkeypatch) -> None:
    routes = {
        (f"{OPENROUTER_API_BASE_URL}/key", ()): httpx.Response(
            200,
            json={"data": {"label": "sk-or-v1-run...ime", "creator_user_id": "user_123"}},
        ),
        (f"{OPENROUTER_API_BASE_URL}/workspaces", ()): httpx.Response(401, json={"error": {"message": "invalid"}}),
    }
    monkeypatch.setattr(
        openrouter_validation_module.httpx,
        "AsyncClient",
        _fake_async_client_factory(routes),
    )

    with pytest.raises(HTTPException) as exc_info:
        await openrouter_validation_module.validate_openrouter_keys(
            openrouter_api_key="sk-or-v1-runtime",
            openrouter_management_key="sk-or-v1-management",
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid OpenRouter management key"


@pytest.mark.anyio
async def test_validate_openrouter_keys_rejects_unrelated_keys(monkeypatch) -> None:
    routes = {
        (f"{OPENROUTER_API_BASE_URL}/key", ()): httpx.Response(
            200,
            json={"data": {"label": "sk-or-v1-run...ime", "creator_user_id": "user_123"}},
        ),
        (f"{OPENROUTER_API_BASE_URL}/workspaces", ()): httpx.Response(
            200,
            json={"data": [{"id": "workspace-1"}]},
        ),
        (f"{OPENROUTER_API_BASE_URL}/keys", (("workspace_id", "workspace-1"),)): httpx.Response(
            200,
            json={"data": [{"label": "sk-or-v1-other", "creator_user_id": "user_999"}]},
        ),
    }
    monkeypatch.setattr(
        openrouter_validation_module.httpx,
        "AsyncClient",
        _fake_async_client_factory(routes),
    )

    with pytest.raises(HTTPException) as exc_info:
        await openrouter_validation_module.validate_openrouter_keys(
            openrouter_api_key="sk-or-v1-runtime",
            openrouter_management_key="sk-or-v1-management",
        )

    assert exc_info.value.status_code == 400
    assert "not visible to the provided management key" in exc_info.value.detail


@pytest.mark.anyio
async def test_validate_openrouter_keys_rejects_ambiguous_multi_workspace_match(monkeypatch) -> None:
    routes = {
        (f"{OPENROUTER_API_BASE_URL}/key", ()): httpx.Response(
            200,
            json={"data": {"label": "sk-or-v1-run...ime", "creator_user_id": "user_123"}},
        ),
        (f"{OPENROUTER_API_BASE_URL}/workspaces", ()): httpx.Response(
            200,
            json={"data": [{"id": "workspace-1"}, {"id": "workspace-2"}]},
        ),
        (f"{OPENROUTER_API_BASE_URL}/keys", (("workspace_id", "workspace-1"),)): httpx.Response(
            200,
            json={"data": [{"label": "sk-or-v1-run...ime", "creator_user_id": "user_123"}]},
        ),
        (f"{OPENROUTER_API_BASE_URL}/keys", (("workspace_id", "workspace-2"),)): httpx.Response(
            200,
            json={"data": [{"label": "sk-or-v1-run...ime", "creator_user_id": "user_123"}]},
        ),
    }
    monkeypatch.setattr(
        openrouter_validation_module.httpx,
        "AsyncClient",
        _fake_async_client_factory(routes),
    )

    with pytest.raises(HTTPException) as exc_info:
        await openrouter_validation_module.validate_openrouter_keys(
            openrouter_api_key="sk-or-v1-runtime",
            openrouter_management_key="sk-or-v1-management",
        )

    assert exc_info.value.status_code == 400
    assert "matched multiple OpenRouter key records" in exc_info.value.detail


@pytest.mark.anyio
async def test_validate_openrouter_keys_rejects_unsafe_workspace_flags(monkeypatch) -> None:
    routes = {
        (f"{OPENROUTER_API_BASE_URL}/key", ()): httpx.Response(
            200,
            json={"data": {"label": "sk-or-v1-run...ime", "creator_user_id": "user_123"}},
        ),
        (f"{OPENROUTER_API_BASE_URL}/workspaces", ()): httpx.Response(
            200,
            json={"data": [{"id": "workspace-1"}]},
        ),
        (f"{OPENROUTER_API_BASE_URL}/keys", (("workspace_id", "workspace-1"),)): httpx.Response(
            200,
            json={"data": [{"label": "sk-or-v1-run...ime", "creator_user_id": "user_123"}]},
        ),
        (f"{OPENROUTER_API_BASE_URL}/workspaces/workspace-1", ()): httpx.Response(
            200,
            json={
                "data": {
                    "is_observability_io_logging_enabled": False,
                    "is_observability_broadcast_enabled": True,
                    "is_data_discount_logging_enabled": False,
                }
            },
        ),
    }
    monkeypatch.setattr(
        openrouter_validation_module.httpx,
        "AsyncClient",
        _fake_async_client_factory(routes),
    )

    with pytest.raises(HTTPException) as exc_info:
        await openrouter_validation_module.validate_openrouter_keys(
            openrouter_api_key="sk-or-v1-runtime",
            openrouter_management_key="sk-or-v1-management",
        )

    assert exc_info.value.status_code == 400
    assert "must disable input/output logging, broadcast, and data discount logging" in exc_info.value.detail


@pytest.mark.anyio
async def test_post_agent_encrypts_both_openrouter_keys_and_persists_metadata(monkeypatch) -> None:
    captured: dict[str, object] = {}
    validated_keys = _validated_keys()

    async def fake_create_agent(
        agent,
        agent_text,
        *,
        source_sha256=None,
        runtime_openrouter_api_key_ciphertext=None,
        management_openrouter_api_key_ciphertext=None,
        openrouter_workspace_id=None,
        openrouter_api_key_label=None,
        openrouter_api_key_creator_user_id=None,
        openrouter_validated_at=None,
        create_pre_screening_job=False,
    ) -> None:
        captured["agent"] = agent
        captured["agent_text"] = agent_text
        captured["runtime_ciphertext"] = runtime_openrouter_api_key_ciphertext
        captured["management_ciphertext"] = management_openrouter_api_key_ciphertext
        captured["workspace_id"] = openrouter_workspace_id
        captured["api_key_label"] = openrouter_api_key_label
        captured["api_key_creator_user_id"] = openrouter_api_key_creator_user_id
        captured["validated_at"] = openrouter_validated_at
        captured["create_pre_screening_job"] = create_pre_screening_job

    upload_endpoint = _patch_upload_dependencies(
        monkeypatch,
        create_agent_impl=fake_create_agent,
        validated_keys=validated_keys,
    )

    response = await upload_endpoint.post_agent(
        _make_request(),
        agent_file=_make_upload_file(),
        public_key="pub",
        file_info="miner-hotkey:1",
        signature="sig",
        name="Agent",
        payment_block_hash="block",
        payment_extrinsic_index="0",
        openrouter_api_key="sk-or-v1-runtime",
        openrouter_management_key="sk-or-v1-management",
    )

    assert response.status == "success"
    assert captured["agent_text"] == "print('hi')\n"
    assert decrypt_agent_secret(captured["runtime_ciphertext"]) == "sk-or-v1-runtime"
    assert decrypt_agent_secret(captured["management_ciphertext"]) == "sk-or-v1-management"
    assert captured["workspace_id"] == validated_keys.workspace_id
    assert captured["api_key_label"] == validated_keys.api_key_label
    assert captured["api_key_creator_user_id"] == validated_keys.api_key_creator_user_id
    assert captured["validated_at"] == validated_keys.validated_at
    assert captured["create_pre_screening_job"] is False


@pytest.mark.anyio
async def test_check_agent_uses_shared_openrouter_validation(monkeypatch) -> None:
    validation_calls: list[tuple[str, str]] = []

    async def fake_create_agent(*args, **kwargs) -> None:
        raise AssertionError("create_agent should not be called by /upload/agent/check")

    upload_endpoint = _patch_upload_dependencies(monkeypatch, create_agent_impl=fake_create_agent)

    async def fake_validate_openrouter_keys(*, openrouter_api_key: str | None, openrouter_management_key: str | None):
        validation_calls.append((openrouter_api_key, openrouter_management_key))
        return _validated_keys()

    monkeypatch.setattr(upload_endpoint, "validate_openrouter_keys", fake_validate_openrouter_keys)

    response = await upload_endpoint.check_agent_post(
        _make_request(),
        agent_file=_make_upload_file(),
        public_key="pub",
        file_info="miner-hotkey:1",
        signature="sig",
        name="Agent",
        openrouter_api_key="sk-or-v1-runtime",
        openrouter_management_key="sk-or-v1-management",
    )

    assert response.status == "success"
    assert validation_calls == [("sk-or-v1-runtime", "sk-or-v1-management")]


def _patch_validator_dependencies(
    monkeypatch,
    *,
    agent_id,
    validator_hotkey: str,
    openrouter_secrets,
    created_evaluations: list | None = None,
    updated_runs: list[EvaluationRun] | None = None,
    handled_evaluations: list | None = None,
):
    evaluation = _make_evaluation(agent_id, validator_hotkey)
    evaluation_runs = [
        _make_evaluation_run(evaluation.evaluation_id, problem_name="problem-1"),
        _make_evaluation_run(evaluation.evaluation_id, problem_name="problem-2"),
    ]

    async def fake_get_next_agent_id(_hotkey: str):
        return agent_id

    async def fake_get_agent_by_id(_agent_id):
        return _make_agent(agent_id)

    async def fake_download_text(_key: str):
        return "print('agent')\n"

    async def fake_create_bundle(_agent_id, _validator_hotkey):
        if created_evaluations is not None:
            created_evaluations.append((_agent_id, _validator_hotkey))
        return evaluation, evaluation_runs

    async def fake_generate_upload_url(_s3_key: str):
        return "https://example.com/upload"

    async def fake_get_openrouter_secrets(_agent_id):
        if isinstance(openrouter_secrets, Exception):
            raise openrouter_secrets
        return openrouter_secrets

    async def fake_update_evaluation_run_by_id(run: EvaluationRun) -> None:
        if updated_runs is not None:
            updated_runs.append(run.model_copy(deep=True))

    async def fake_handle_evaluation_if_finished(evaluation_id) -> None:
        if handled_evaluations is not None:
            handled_evaluations.append(evaluation_id)

    async def fake_get_internal_flags_parsed(_flags):
        return {
            InternalFlagName.VALIDATORS_PAUSED: False,
            InternalFlagName.BLACKLISTED_VALIDATORS: [],
        }

    monkeypatch.setattr(validator_endpoint, "get_internal_flags_parsed", fake_get_internal_flags_parsed)
    monkeypatch.setattr(validator_endpoint, "record_validator_heartbeat", lambda _validator: None)
    monkeypatch.setattr(
        validator_endpoint,
        "get_next_agent_id_awaiting_evaluation_for_validator_hotkey",
        fake_get_next_agent_id,
    )
    monkeypatch.setattr(validator_endpoint, "get_agent_by_id", fake_get_agent_by_id)
    monkeypatch.setattr(validator_endpoint, "download_text_file_from_s3", fake_download_text)
    monkeypatch.setattr(validator_endpoint, "create_new_evaluation_and_evaluation_runs", fake_create_bundle)
    monkeypatch.setattr(validator_endpoint, "generate_presigned_upload_url", fake_generate_upload_url)
    monkeypatch.setattr(validator_endpoint, "get_openrouter_secrets_for_agent_id", fake_get_openrouter_secrets)
    monkeypatch.setattr(validator_endpoint, "read_execution_spec_metadata", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(validator_endpoint, "update_evaluation_run_by_id", fake_update_evaluation_run_by_id)
    monkeypatch.setattr(validator_endpoint, "handle_evaluation_if_finished", fake_handle_evaluation_if_finished)

    return evaluation, evaluation_runs


@pytest.mark.anyio
async def test_validator_request_evaluation_returns_decrypted_keys_and_hash(monkeypatch) -> None:
    agent_id = uuid4()
    validator_hotkey = "screener-1-test"
    _patch_validator_dependencies(
        monkeypatch,
        agent_id=agent_id,
        validator_hotkey=validator_hotkey,
        openrouter_secrets=AgentOpenRouterSecrets(
            runtime_api_key="sk-or-v1-runtime",
            management_api_key="sk-or-v1-management",
            workspace_id="workspace-1",
            api_key_label="sk-or-v1-run...ime",
            api_key_creator_user_id="user_123",
            validated_at=datetime.now(timezone.utc),
        ),
    )
    validator = validator_endpoint.Validator(
        session_id=uuid4(),
        name="validator",
        hotkey=validator_hotkey,
        time_connected=datetime.now(timezone.utc),
        ip_address="127.0.0.1",
    )

    response = await validator_endpoint.validator_request_evaluation(
        ValidatorRequestEvaluationRequest(),
        validator=validator,
    )

    assert response is not None
    assert response.openrouter_config is not None
    assert response.openrouter_config.api_key == "sk-or-v1-runtime"
    assert response.openrouter_config.management_key == "sk-or-v1-management"
    assert response.openrouter_config.workspace_id == "workspace-1"
    assert (
        response.openrouter_config.expected_api_key_sha256
        == "37e8a8555e4fd04be40028f8c53351c085c21dee6eda0f7297a42cbb5082f9c9"
    )
    assert validator.current_evaluation_id is not None
    assert validator.current_agent is not None


@pytest.mark.anyio
async def test_validator_request_evaluation_allows_missing_openrouter_record(monkeypatch) -> None:
    agent_id = uuid4()
    validator_hotkey = "screener-1-test"
    _patch_validator_dependencies(
        monkeypatch,
        agent_id=agent_id,
        validator_hotkey=validator_hotkey,
        openrouter_secrets=None,
    )
    validator = validator_endpoint.Validator(
        session_id=uuid4(),
        name="validator",
        hotkey=validator_hotkey,
        time_connected=datetime.now(timezone.utc),
        ip_address="127.0.0.1",
    )

    response = await validator_endpoint.validator_request_evaluation(
        ValidatorRequestEvaluationRequest(),
        validator=validator,
    )

    assert response is not None
    assert response.openrouter_config is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "secret_error",
    [
        AgentKeyDecryptError("bad ciphertext"),
        AgentKeyEncryptionConfigError("missing master key"),
    ],
)
async def test_validator_request_evaluation_skips_assignment_when_secret_is_unreadable(
    monkeypatch, secret_error
) -> None:
    agent_id = uuid4()
    validator_hotkey = "screener-1-test"
    created_evaluations: list = []
    updated_runs: list[EvaluationRun] = []
    handled_evaluations: list = []
    _patch_validator_dependencies(
        monkeypatch,
        agent_id=agent_id,
        validator_hotkey=validator_hotkey,
        openrouter_secrets=secret_error,
        created_evaluations=created_evaluations,
        updated_runs=updated_runs,
        handled_evaluations=handled_evaluations,
    )
    validator = validator_endpoint.Validator(
        session_id=uuid4(),
        name="validator",
        hotkey=validator_hotkey,
        time_connected=datetime.now(timezone.utc),
        ip_address="127.0.0.1",
    )

    response = await validator_endpoint.validator_request_evaluation(
        ValidatorRequestEvaluationRequest(),
        validator=validator,
    )

    assert response is None
    assert validator.current_evaluation_id is None
    assert validator.current_agent is None
    assert created_evaluations == []
    assert updated_runs == []
    assert handled_evaluations == []
