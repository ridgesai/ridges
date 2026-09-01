from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

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


def _competition_client(competitions: list[dict]) -> MagicMock:
    response = MagicMock(status_code=200, text="ok")
    response.json.return_value = competitions
    client = MagicMock()
    client.get.return_value = response
    return client


def test_select_upload_competition_requires_deliberate_choice(monkeypatch) -> None:
    with pytest.raises(upload_module.click.ClickException, match="No competition"):
        upload_module._select_upload_competition(
            _competition_client([]),
            api_url="https://example.test",
            requested_set_id=None,
        )

    assert (
        upload_module._select_upload_competition(
            _competition_client([{"set_id": 2, "name": "Two"}, {"set_id": 9, "name": "Nine"}]),
            api_url="https://example.test",
            requested_set_id=9,
        )
        == 9
    )
    with pytest.raises(upload_module.click.ClickException, match="not accepting"):
        upload_module._select_upload_competition(
            _competition_client([{"set_id": 2, "name": "Two"}]),
            api_url="https://example.test",
            requested_set_id=9,
        )

    # Without --competition and without a TTY, fail fast
    monkeypatch.setattr(upload_module.sys, "stdin", MagicMock(isatty=MagicMock(return_value=False)))
    with pytest.raises(upload_module.click.ClickException) as exc_info:
        upload_module._select_upload_competition(
            _competition_client([{"set_id": 7, "name": "Seven"}]),
            api_url="https://example.test",
            requested_set_id=None,
        )
    assert "7 (Seven)" in str(exc_info.value)
    assert "--competition INTEGER" in str(exc_info.value)

    # Interactive sessions prompt for an explicit choice, even with a single competition.
    monkeypatch.setattr(upload_module.sys, "stdin", MagicMock(isatty=MagicMock(return_value=True)))
    prompt = MagicMock(return_value="7")
    monkeypatch.setattr(upload_module.Prompt, "ask", prompt)
    assert (
        upload_module._select_upload_competition(
            _competition_client([{"set_id": 7, "name": "Seven"}]),
            api_url="https://example.test",
            requested_set_id=None,
        )
        == 7
    )
    assert prompt.call_args.kwargs["choices"] == ["7"]

    prompt.return_value = "9"
    assert (
        upload_module._select_upload_competition(
            _competition_client([{"set_id": 2, "name": "Two"}, {"set_id": 9, "name": "Nine"}]),
            api_url="https://example.test",
            requested_set_id=None,
        )
        == 9
    )
    assert prompt.call_args.kwargs["choices"] == ["2", "9"]


def test_latest_agent_preview_is_filtered_to_selected_set() -> None:
    client = _competition_client([])
    client.get.return_value.json.return_value = [
        {"set_id": 1, "name": "Set One", "version_num": 8},
        {"set_id": 2, "name": "Old", "version_num": 1},
        {"set_id": 2, "name": "Current", "version_num": 3},
    ]

    name, version = upload_module._resolve_upload_name_and_version(
        client,
        api_url="https://example.test",
        hotkey="hotkey",
        set_id=2,
    )

    assert (name, version) == ("Current", 4)


def test_check_upload_allowed_sends_both_openrouter_keys(tmp_path: Path) -> None:
    quote_response = {
        "quote_id": "quote-123",
        "amount_alpha_rao": 120_344_620_287_164,
        "payment_netuid": 62,
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


def test_check_upload_allowed_requests_specific_credit(tmp_path: Path) -> None:
    credit_response = {
        "payment_method": "credit",
        "credit_id": "67c64261-a579-4be8-8cb5-63ad3eeb669a",
        "amount_alpha_rao": 0,
    }
    client = _FakeClient(_FakeResponse(200, json_data=credit_response))
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

    response = upload_module._check_upload_allowed(
        client,
        target=target,
        pending=pending,
        credentials=credentials,
        use_credit=True,
        credit_id=credit_response["credit_id"],
    )

    assert response == credit_response
    assert client.calls[0]["data"]["use_credit"] == "true"
    assert client.calls[0]["data"]["credit_id"] == credit_response["credit_id"]


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


def test_credit_upload_payload_excludes_burn_fields() -> None:
    pending = upload_module.PendingUpload(
        name="agent",
        version_num=0,
        file_info="hk:hash:0",
        public_key="pub",
        signature="sig",
    )
    receipt = upload_module.CreditReceipt(credit_id="67c64261-a579-4be8-8cb5-63ad3eeb669a")
    credentials = upload_module.OpenRouterUploadCredentials(
        runtime_api_key="sk-or-v1-runtime",
        management_key="sk-or-v1-management",
    )

    payload = upload_module._upload_payload(pending=pending, receipt=receipt, credentials=credentials)

    assert payload["credit_id"] == receipt.credit_id
    assert "quote_id" not in payload
    assert "payment_block_hash" not in payload
    assert "payment_extrinsic_index" not in payload


def test_upload_result_prints_server_message(monkeypatch) -> None:
    message = (
        "Upload credit 67c64261-a579-4be8-8cb5-63ad3eeb669a was already used for agent "
        "e71efacd-e9b7-4f1e-9c43-2a453419c07d. No new agent was created."
    )
    print_result = MagicMock()
    monkeypatch.setattr(upload_module.console, "print", print_result)

    upload_module._handle_upload_result(_FakeResponse(200, json_data={"message": message}), name="agent")

    panel = print_result.call_args.args[0]
    assert message in panel.renderable


def test_upload_command_credit_path_never_attempts_burn(monkeypatch, tmp_path: Path) -> None:
    credit_id = "67c64261-a579-4be8-8cb5-63ad3eeb669a"
    wallet = MagicMock()
    wallet.hotkey.ss58_address = "5FHhot"
    target = upload_module.UploadTarget(
        api_url="https://agent-upload.ridges.ai",
        agent_path=tmp_path / "agent.py",
        file_content=b"print('hi')\n",
        content_hash="abc123",
    )
    pending = upload_module.PendingUpload(
        name="agent",
        version_num=0,
        file_info="5FHhot:abc123:0",
        public_key="pub",
        signature="sig",
    )
    credentials = upload_module.OpenRouterUploadCredentials("runtime", "management")
    client = MagicMock()
    client_context = MagicMock()
    client_context.__enter__.return_value = client

    monkeypatch.setattr(upload_module, "_resolve_wallet_and_target", MagicMock(return_value=(wallet, target)))
    monkeypatch.setattr(upload_module, "_resolve_openrouter_upload_credentials", MagicMock(return_value=credentials))
    monkeypatch.setattr(upload_module, "_print_upload_preview", MagicMock())
    monkeypatch.setattr(upload_module, "_select_upload_competition", MagicMock(return_value=1))
    monkeypatch.setattr(upload_module, "_prepare_pending_upload", MagicMock(return_value=pending))
    monkeypatch.setattr(
        upload_module,
        "_check_upload_allowed",
        MagicMock(
            return_value={"payment_method": "credit", "credit_id": credit_id, "amount_alpha_rao": 0, "set_id": 1}
        ),
    )
    monkeypatch.setattr(upload_module.httpx, "Client", MagicMock(return_value=client_context))
    unlock = MagicMock(side_effect=AssertionError("credit path must not unlock the coldkey"))
    burn = MagicMock(side_effect=AssertionError("credit path must not burn alpha"))
    execute = MagicMock()
    monkeypatch.setattr(upload_module, "_unlock_coldkey", unlock)
    monkeypatch.setattr(upload_module, "_submit_eval_payment", burn)
    monkeypatch.setattr(upload_module, "_execute_upload", execute)

    result = CliRunner().invoke(upload_module.upload, ["--use-credit"], obj={})

    assert result.exit_code == 0, result.output
    unlock.assert_not_called()
    burn.assert_not_called()
    receipt = execute.call_args.kwargs["receipt"]
    assert isinstance(receipt, upload_module.CreditReceipt)
    assert receipt.credit_id == credit_id


def test_submit_eval_payment_composes_burn_alpha(monkeypatch):
    from unittest.mock import MagicMock

    calls = {}

    fake_substrate = MagicMock()
    fake_substrate.compose_call = MagicMock(side_effect=lambda **kw: calls.update(kw) or "payload")
    fake_substrate.create_signed_extrinsic = MagicMock(return_value="signed")
    fake_substrate.submit_extrinsic = MagicMock(
        return_value=MagicMock(block_hash="0xblock", extrinsic_idx=4, is_success=True)
    )
    fake_subtensor = MagicMock(substrate=fake_substrate)
    monkeypatch.setattr(upload_module, "Subtensor", MagicMock(return_value=fake_subtensor), raising=False)

    import sys

    fake_bt = MagicMock()
    fake_bt.Subtensor = MagicMock(return_value=fake_subtensor)
    monkeypatch.setitem(sys.modules, "bittensor", fake_bt)

    wallet = MagicMock()
    wallet.coldkey = "ck"
    wallet.hotkey.ss58_address = "5FHhot"

    details = {"amount_alpha_rao": 120_344_620_287_164, "payment_netuid": 777, "quote_id": "q1"}
    receipt = upload_module._submit_eval_payment(wallet=wallet, payment_method_details=details)

    assert calls["call_module"] == "SubtensorModule"
    assert calls["call_function"] == "burn_alpha"
    assert calls["call_params"]["netuid"] == 777
    assert calls["call_params"]["amount"] == 120_344_620_287_164
    assert receipt.block_hash == "0xblock"
    assert receipt.extrinsic_index == 4
    assert receipt.quote_id == "q1"


def test_submit_eval_payment_surfaces_failed_extrinsic(monkeypatch):
    from unittest.mock import MagicMock

    fake_substrate = MagicMock()
    fake_substrate.compose_call.return_value = "payload"
    fake_substrate.create_signed_extrinsic.return_value = "signed"
    fake_substrate.submit_extrinsic.return_value = MagicMock(
        block_hash="0xblock",
        extrinsic_idx=4,
        is_success=False,
        error_message="NotEnoughBalanceToPayFees",
    )
    fake_subtensor = MagicMock(substrate=fake_substrate)

    import sys

    fake_bt = MagicMock()
    fake_bt.Subtensor.return_value = fake_subtensor
    monkeypatch.setitem(sys.modules, "bittensor", fake_bt)

    wallet = MagicMock()
    wallet.coldkey = "ck"
    wallet.hotkey.ss58_address = "5FHhot"
    details = {"amount_alpha_rao": 1_000, "payment_netuid": 62, "quote_id": "q1"}

    with pytest.raises(
        upload_module.click.ClickException,
        match="Alpha burn failed on-chain: NotEnoughBalanceToPayFees",
    ):
        upload_module._submit_eval_payment(wallet=wallet, payment_method_details=details)


def test_upload_selects_before_name_keys_or_payment(monkeypatch, tmp_path: Path) -> None:
    wallet = MagicMock()
    wallet.hotkey.ss58_address = "hotkey"
    target = upload_module.UploadTarget(
        api_url="https://example.test",
        agent_path=tmp_path / "agent.py",
        file_content=b"print('hi')\n",
        content_hash="source",
    )
    client_context = MagicMock()
    client_context.__enter__.return_value = MagicMock()
    monkeypatch.setattr(upload_module, "_resolve_wallet_and_target", MagicMock(return_value=(wallet, target)))
    monkeypatch.setattr(upload_module.httpx, "Client", MagicMock(return_value=client_context))
    monkeypatch.setattr(
        upload_module,
        "_select_upload_competition",
        MagicMock(side_effect=upload_module.click.ClickException("No competition is accepting uploads")),
    )
    prepare = MagicMock(side_effect=AssertionError("name/signing must happen after selection"))
    credentials = MagicMock(side_effect=AssertionError("keys must happen after selection"))
    unlock = MagicMock(side_effect=AssertionError("payment must happen after selection"))
    submit = MagicMock(side_effect=AssertionError("payment must happen after selection"))
    monkeypatch.setattr(upload_module, "_prepare_pending_upload", prepare)
    monkeypatch.setattr(upload_module, "_resolve_openrouter_upload_credentials", credentials)
    monkeypatch.setattr(upload_module, "_unlock_coldkey", unlock)
    monkeypatch.setattr(upload_module, "_submit_eval_payment", submit)

    result = CliRunner().invoke(upload_module.upload, [], obj={})

    assert result.exit_code != 0
    assert "No competition" in result.output
    prepare.assert_not_called()
    credentials.assert_not_called()
    unlock.assert_not_called()
    submit.assert_not_called()


def test_upload_pins_preflight_set_for_final_submission(monkeypatch, tmp_path: Path) -> None:
    wallet = MagicMock()
    wallet.hotkey.ss58_address = "hotkey"
    target = upload_module.UploadTarget(
        api_url="https://example.test",
        agent_path=tmp_path / "agent.py",
        file_content=b"print('hi')\n",
        content_hash="source",
    )
    pending = upload_module.PendingUpload("Agent", 0, "file-info", "public", "signature")
    credentials = upload_module.OpenRouterUploadCredentials("runtime", "management")
    client = MagicMock()
    client_context = MagicMock()
    client_context.__enter__.return_value = client
    preflight = MagicMock(
        return_value={
            "payment_method": "credit",
            "credit_id": "credit-id",
            "amount_alpha_rao": 0,
            "set_id": 12,
        }
    )
    execute = MagicMock()
    monkeypatch.setattr(upload_module, "_resolve_wallet_and_target", MagicMock(return_value=(wallet, target)))
    monkeypatch.setattr(upload_module.httpx, "Client", MagicMock(return_value=client_context))
    monkeypatch.setattr(upload_module, "_select_upload_competition", MagicMock(return_value=12))
    monkeypatch.setattr(upload_module, "_prepare_pending_upload", MagicMock(return_value=pending))
    monkeypatch.setattr(upload_module, "_resolve_openrouter_upload_credentials", MagicMock(return_value=credentials))
    monkeypatch.setattr(upload_module, "_print_upload_preview", MagicMock())
    monkeypatch.setattr(upload_module, "_print_credit_receipt", MagicMock())
    monkeypatch.setattr(upload_module, "_check_upload_allowed", preflight)
    monkeypatch.setattr(upload_module, "_execute_upload", execute)

    result = CliRunner().invoke(upload_module.upload, ["--competition", "12", "--use-credit"], obj={})

    assert result.exit_code == 0, result.output
    assert preflight.call_args.kwargs["set_id"] == 12
    assert execute.call_args.kwargs["set_id"] == 12


@pytest.mark.parametrize(
    ("returned_set_id", "expected_message"),
    [
        (13, "changed the selected competition"),
        (None, "did not return the selected competition"),
        ("12", "did not return the selected competition"),
    ],
)
def test_upload_rejects_invalid_preflight_set_before_payment(
    monkeypatch,
    tmp_path: Path,
    returned_set_id,
    expected_message: str,
) -> None:
    wallet = MagicMock()
    wallet.hotkey.ss58_address = "hotkey"
    target = upload_module.UploadTarget(
        api_url="https://example.test",
        agent_path=tmp_path / "agent.py",
        file_content=b"print('hi')\n",
        content_hash="source",
    )
    pending = upload_module.PendingUpload("Agent", 0, "file-info", "public", "signature")
    credentials = upload_module.OpenRouterUploadCredentials("runtime", "management")
    client_context = MagicMock()
    client_context.__enter__.return_value = MagicMock()
    monkeypatch.setattr(upload_module, "_resolve_wallet_and_target", MagicMock(return_value=(wallet, target)))
    monkeypatch.setattr(upload_module.httpx, "Client", MagicMock(return_value=client_context))
    monkeypatch.setattr(upload_module, "_select_upload_competition", MagicMock(return_value=12))
    monkeypatch.setattr(upload_module, "_prepare_pending_upload", MagicMock(return_value=pending))
    monkeypatch.setattr(upload_module, "_resolve_openrouter_upload_credentials", MagicMock(return_value=credentials))
    monkeypatch.setattr(upload_module, "_print_upload_preview", MagicMock())
    monkeypatch.setattr(
        upload_module,
        "_check_upload_allowed",
        MagicMock(return_value={"payment_method": "burn", "set_id": returned_set_id}),
    )
    unlock = MagicMock(side_effect=AssertionError("changed set must stop before payment"))
    submit = MagicMock(side_effect=AssertionError("changed set must stop before payment"))
    monkeypatch.setattr(upload_module, "_unlock_coldkey", unlock)
    monkeypatch.setattr(upload_module, "_submit_eval_payment", submit)

    result = CliRunner().invoke(upload_module.upload, [], obj={})

    assert result.exit_code != 0
    assert expected_message in result.output
    unlock.assert_not_called()
    submit.assert_not_called()


def test_resume_upload_passes_selected_set_to_final(monkeypatch, tmp_path: Path) -> None:
    wallet = MagicMock()
    wallet.hotkey.ss58_address = "hotkey"
    target = upload_module.UploadTarget(
        api_url="https://example.test",
        agent_path=tmp_path / "agent.py",
        file_content=b"print('hi')\n",
        content_hash="source",
    )
    pending = upload_module.PendingUpload("Agent", 0, "file-info", "public", "signature")
    credentials = upload_module.OpenRouterUploadCredentials("runtime", "management")
    client_context = MagicMock()
    client_context.__enter__.return_value = MagicMock()
    execute = MagicMock()
    monkeypatch.setattr(upload_module, "_resolve_wallet_and_target", MagicMock(return_value=(wallet, target)))
    monkeypatch.setattr(upload_module.httpx, "Client", MagicMock(return_value=client_context))
    monkeypatch.setattr(upload_module, "_select_upload_competition", MagicMock(return_value=17))
    monkeypatch.setattr(upload_module, "_prepare_pending_upload", MagicMock(return_value=pending))
    monkeypatch.setattr(upload_module, "_resolve_openrouter_upload_credentials", MagicMock(return_value=credentials))
    monkeypatch.setattr(upload_module, "_print_upload_preview", MagicMock())
    monkeypatch.setattr(upload_module, "_execute_upload", execute)

    result = CliRunner().invoke(
        upload_module.resume_upload,
        [
            "--competition",
            "17",
            "--quote-id",
            "quote",
            "--payment-block-hash",
            "block",
            "--payment-extrinsic-index",
            "3",
        ],
        obj={},
    )

    assert result.exit_code == 0, result.output
    assert execute.call_args.kwargs["set_id"] == 17
    assert execute.call_args.kwargs["run_check"] is False
