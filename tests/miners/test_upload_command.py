from __future__ import annotations

from pathlib import Path

import miners.cli.commands.upload as upload_module


def test_resolve_openrouter_upload_credentials_prefers_cli_values(monkeypatch) -> None:
    monkeypatch.setenv("RIDGES_OPENROUTER_API_KEY", "env-runtime")
    monkeypatch.setenv("RIDGES_OPENROUTER_MANAGEMENT_KEY", "env-management")

    credentials = upload_module._resolve_openrouter_upload_credentials(
        openrouter_api_key="cli-runtime",
        openrouter_management_key="cli-management",
    )

    assert credentials.runtime_api_key == "cli-runtime"
    assert credentials.management_key == "cli-management"


def test_resolve_openrouter_upload_credentials_uses_env_then_prompt(monkeypatch) -> None:
    prompts: list[tuple[str, bool]] = []

    def fake_prompt(message: str, password: bool = False, default: str | None = None) -> str:
        prompts.append((message, password))
        if "management" in message.lower():
            return "prompt-management"
        return "prompt-runtime"

    monkeypatch.setenv("RIDGES_OPENROUTER_API_KEY", "env-runtime")
    monkeypatch.delenv("RIDGES_OPENROUTER_MANAGEMENT_KEY", raising=False)
    monkeypatch.setattr(upload_module.Prompt, "ask", staticmethod(fake_prompt))

    credentials = upload_module._resolve_openrouter_upload_credentials(
        openrouter_api_key=None,
        openrouter_management_key=None,
    )

    assert credentials.runtime_api_key == "env-runtime"
    assert credentials.management_key == "prompt-management"
    assert prompts == [("🔐 Enter your OpenRouter management key", True)]


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "ok", json_data: dict | None = None) -> None:
        self.status_code = status_code
        self.text = text
        self._json_data = json_data or {}

    def json(self) -> dict:
        return self._json_data


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls: list[dict] = []

    def post(self, url: str, *, files=None, data=None, json=None, timeout=None):
        self.calls.append({"url": url, "files": files, "data": data, "json": json, "timeout": timeout})
        return self.response


def test_check_upload_allowed_sends_both_openrouter_keys(tmp_path: Path) -> None:
    quote_response = {
        "quote_id": "quote-123",
        "amount_rao": 123,
        "send_address": "5Send",
        "expires_at": "2026-06-10T00:00:00Z",
    }
    client = _FakeClient(_FakeResponse(200, json_data=quote_response))
    target = upload_module.UploadTarget(
        api_url="https://agent-upload.ridges.ai",
        agent_path=tmp_path / "agent.py",
        file_content=b"print('hi')\n",
        content_hash="abc123",
    )
    pending = upload_module.PendingUpload(
        name="agent",
        version_num=0,
        file_info="hk:hash:0",
        public_key="pub",
        signature="sig",
    )
    credentials = upload_module.OpenRouterUploadCredentials(
        runtime_api_key="sk-or-v1-runtime",
        management_key="sk-or-v1-management",
    )

    quote = upload_module._check_upload_allowed(client, target=target, pending=pending, credentials=credentials)

    assert quote == quote_response
    assert len(client.calls) == 1
    assert client.calls[0]["data"]["openrouter_api_key"] == "sk-or-v1-runtime"
    assert client.calls[0]["data"]["openrouter_management_key"] == "sk-or-v1-management"
    assert "payment_time" not in client.calls[0]["data"]


def test_upload_payload_includes_both_openrouter_keys() -> None:
    pending = upload_module.PendingUpload(
        name="agent",
        version_num=0,
        file_info="hk:hash:0",
        public_key="pub",
        signature="sig",
    )
    receipt = upload_module.PaymentReceipt(
        block_hash="0xabc",
        extrinsic_index=5,
        quote_id="quote-123",
    )
    credentials = upload_module.OpenRouterUploadCredentials(
        runtime_api_key="sk-or-v1-runtime",
        management_key="sk-or-v1-management",
    )

    payload = upload_module._upload_payload(
        pending=pending,
        receipt=receipt,
        credentials=credentials,
    )

    assert payload["openrouter_api_key"] == "sk-or-v1-runtime"
    assert payload["openrouter_management_key"] == "sk-or-v1-management"
    assert payload["quote_id"] == "quote-123"
    assert "payment_time" not in payload
