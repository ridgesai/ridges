from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from bittensor_wallet.keypair import Keypair
from click.testing import CliRunner

import miners.cli.commands.upload as upload_module
import miners.cli.commands.upload_payment as payment_module
from utils.upload_ticket import confirm_signing_string, purchase_signing_string

KEYPAIR = Keypair.create_from_seed("0x" + "ab" * 32)
CANON_HASH = "0x" + "ab" * 32


def _flat(output: str) -> str:
    """Output with wrapping undone, for asserting on prose."""
    return " ".join(output.split())


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
    monkeypatch.setattr(payment_module, "_unlock_coldkey", unlock)
    monkeypatch.setattr(payment_module, "_submit_eval_payment", burn)
    monkeypatch.setattr(upload_module, "_execute_upload", execute)

    result = CliRunner().invoke(upload_module.upload, ["--use-credit"], obj={})

    assert result.exit_code == 0, result.output
    unlock.assert_not_called()
    burn.assert_not_called()
    receipt = execute.call_args.kwargs["receipt"]
    assert isinstance(receipt, upload_module.CreditReceipt)
    assert receipt.credit_id == credit_id


def _fake_chain(monkeypatch, *, canonical_hash: str = "0xblock") -> MagicMock:
    """A chain where the burn lands in block 100 and finality reaches it on the second poll."""
    import sys

    fake_substrate = MagicMock()
    fake_substrate.compose_call.return_value = "payload"
    fake_substrate.create_signed_extrinsic.return_value = "signed"
    fake_substrate.submit_extrinsic.return_value = MagicMock(block_hash="0xblock", extrinsic_idx=4, is_success=True)
    fake_substrate.get_block_number.side_effect = lambda block_hash: {"0xblock": 100, "0xf1": 99, "0xf2": 100}[
        block_hash
    ]
    fake_substrate.get_chain_finalised_head.side_effect = ["0xf1", "0xf2"]
    fake_substrate.get_block_hash.return_value = canonical_hash
    fake_bt = MagicMock()
    fake_bt.Subtensor.return_value = MagicMock(substrate=fake_substrate)
    monkeypatch.setitem(sys.modules, "bittensor", fake_bt)
    monkeypatch.setattr(payment_module.time, "sleep", MagicMock())
    return fake_substrate


def _burner() -> MagicMock:
    wallet = MagicMock()
    wallet.coldkey = "ck"
    wallet.hotkey.ss58_address = "5FHhot"
    return wallet


def test_submit_eval_payment_composes_burn_alpha(monkeypatch):
    fake_substrate = _fake_chain(monkeypatch)
    details = {"amount_alpha_rao": 120_344_620_287_164, "payment_netuid": 777, "quote_id": "q1"}
    receipt = payment_module._submit_eval_payment(wallet=_burner(), payment_method_details=details)

    calls = fake_substrate.compose_call.call_args.kwargs
    assert calls["call_module"] == "SubtensorModule"
    assert calls["call_function"] == "burn_alpha"
    assert calls["call_params"]["netuid"] == 777
    assert calls["call_params"]["amount"] == 120_344_620_287_164
    assert receipt.block_hash == "0xblock"
    assert receipt.extrinsic_index == 4
    assert receipt.quote_id == "q1"


def test_submit_eval_payment_reports_inclusion_then_waits_for_finality(monkeypatch):
    fake_substrate = _fake_chain(monkeypatch)
    included: list = []
    details = {"amount_alpha_rao": 1_000, "payment_netuid": 62, "quote_id": "q1"}
    receipt = payment_module._submit_eval_payment(
        wallet=_burner(), payment_method_details=details, on_included=included.append
    )

    assert fake_substrate.submit_extrinsic.call_args.kwargs == {"wait_for_inclusion": True}
    assert included == [receipt], "the receipt is known as soon as the burn is in a block"
    assert fake_substrate.get_chain_finalised_head.call_count == 2, "polled until finality reached block 100"
    assert fake_substrate.get_block_hash.call_args.args == (100,)


def test_submit_eval_payment_refuses_a_replaced_block(monkeypatch):
    _fake_chain(monkeypatch, canonical_hash="0xother")
    details = {"amount_alpha_rao": 1_000, "payment_netuid": 62, "quote_id": "q1"}
    with pytest.raises(upload_module.click.ClickException, match="replaced"):
        payment_module._submit_eval_payment(wallet=_burner(), payment_method_details=details)


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
        payment_module._submit_eval_payment(wallet=wallet, payment_method_details=details)


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
    monkeypatch.setattr(payment_module, "_unlock_coldkey", unlock)
    monkeypatch.setattr(payment_module, "_submit_eval_payment", submit)

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
    monkeypatch.setattr(payment_module, "_unlock_coldkey", unlock)
    monkeypatch.setattr(payment_module, "_submit_eval_payment", submit)

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
    monkeypatch.setattr(upload_module, "_resolve_wallet", MagicMock(return_value=wallet))
    monkeypatch.setattr(upload_module, "_resolve_target", MagicMock(return_value=target))
    monkeypatch.setattr(upload_module.httpx, "Client", MagicMock(return_value=client_context))
    monkeypatch.setattr(upload_module, "_select_upload_competition", MagicMock(return_value=17))
    monkeypatch.setattr(upload_module, "_prepare_pending_upload", MagicMock(return_value=pending))
    monkeypatch.setattr(upload_module, "_resolve_openrouter_upload_credentials", MagicMock(return_value=credentials))
    monkeypatch.setattr(upload_module, "_print_upload_preview", MagicMock())
    monkeypatch.setattr(upload_module, "_execute_upload", execute)
    confirm = MagicMock()
    monkeypatch.setattr(upload_module, "_confirm_burn", confirm)
    monkeypatch.setattr(upload_module, "_purchase_quote", MagicMock(return_value=None))

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
    assert confirm.call_args.kwargs["receipt"].quote_id == "quote"


def _keypair_wallet() -> MagicMock:
    wallet = MagicMock()
    wallet.hotkey = KEYPAIR
    return wallet


def test_check_upload_allowed_sends_pricing_version(tmp_path: Path) -> None:
    target = upload_module.UploadTarget("https://example.test", tmp_path / "agent.py", b"x", "h")
    pending = upload_module.PendingUpload("Agent", 0, "file-info", "public", "signature")
    client = _FakeClient(_FakeResponse(200, json_data={"payment_method": "burn"}))
    upload_module._check_upload_allowed(
        client,
        target=target,
        pending=pending,
        credentials=upload_module.OpenRouterUploadCredentials("r", "m"),
        set_id=1,
    )
    assert client.calls[0]["data"]["pricing_version"] == 2


def test_open_quote_exists_is_explained() -> None:
    detail = {"code": "open_quote_exists", "quote_id": "q-1", "expires_at": "2026-10-06T12:15:00+00:00"}
    with pytest.raises(upload_module.click.ClickException, match="q-1"):
        upload_module._raise_if_open_quote_exists(_FakeResponse(409, json_data={"detail": detail}))
    upload_module._raise_if_open_quote_exists(_FakeResponse(409, json_data={"detail": "Competition 1 is draining"}))


def test_confirm_burn_posts_signed_canonical_receipt() -> None:
    client = _FakeClient(_FakeResponse(200, json_data={"status": "confirmed"}))
    receipt = upload_module.PaymentReceipt(block_hash="0x" + "AB" * 32, extrinsic_index=7, quote_id="q")
    upload_module._confirm_burn(client, api_url="https://example.test", wallet=_keypair_wallet(), receipt=receipt)
    body = client.calls[0]["json"]
    assert client.calls[0]["url"] == "https://example.test/upload/payment/confirm"
    assert (body["payment_block_hash"], body["payment_extrinsic_index"]) == (CANON_HASH, 7)
    message = confirm_signing_string(KEYPAIR.ss58_address, "q", CANON_HASH, "7")
    assert KEYPAIR.verify(message, bytes.fromhex(body["signature"]))


def test_confirm_burn_rejects_a_malformed_receipt_cleanly() -> None:
    client = _FakeClient(_FakeResponse(200))
    receipt = upload_module.PaymentReceipt(block_hash="block", extrinsic_index=7, quote_id="q")
    with pytest.raises(upload_module.click.ClickException, match="64 hex"):
        upload_module._confirm_burn(client, api_url="https://x", wallet=_keypair_wallet(), receipt=receipt)
    assert client.calls == []


def test_confirm_burn_retries_server_errors(monkeypatch) -> None:
    monkeypatch.setattr(payment_module.time, "sleep", MagicMock())
    responses = iter([_FakeResponse(503), _FakeResponse(200)])

    class _Client(_FakeClient):
        def post(self, url, **kwargs):
            self.calls.append({"url": url, **kwargs})
            return next(responses)

    client = _Client(_FakeResponse(200))
    receipt = upload_module.PaymentReceipt(block_hash=CANON_HASH, extrinsic_index=7, quote_id="q")
    upload_module._confirm_burn(client, api_url="https://x", wallet=_keypair_wallet(), receipt=receipt)
    assert len(client.calls) == 2


def test_confirm_burn_does_not_retry_client_errors(monkeypatch) -> None:
    monkeypatch.setattr(payment_module.time, "sleep", MagicMock())
    client = _FakeClient(_FakeResponse(402, text="burn_not_reported"))
    receipt = upload_module.PaymentReceipt(block_hash=CANON_HASH, extrinsic_index=7, quote_id="q")
    with pytest.raises(upload_module.click.ClickException, match="burn_not_reported"):
        upload_module._confirm_burn(client, api_url="https://x", wallet=_keypair_wallet(), receipt=receipt)
    assert len(client.calls) == 1


def test_quote_close_to_expiry_is_cancelled_and_not_burned() -> None:
    client = _FakeClient(_FakeResponse(200))
    details = {"quote_id": "q", "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()}
    with pytest.raises(upload_module.click.ClickException, match="nothing was burned"):
        payment_module._ensure_quote_fresh(
            client, api_url="https://x", wallet=_keypair_wallet(), payment_method_details=details
        )
    assert client.calls[0]["url"] == "https://x/upload/quote/q/cancel"


def _market(price_usd: float = 5.0) -> payment_module.PriceSnapshot:
    return payment_module.PriceSnapshot(
        price_usd=price_usd, alpha_price_usd=0.5, floor_usd=5.0, half_life_minutes=30.0, multiplier=1.148698354997035
    )


def _burn_upload_mocks(
    monkeypatch,
    tmp_path: Path,
    *,
    proceed: bool | list[bool],
    shortfalls: int = 0,
    amount_alpha_rao: int = 1,
    price_usd: float = 5.0,
    live_price_usd: float | None = None,
    short_price_usd: float = 5.74,
) -> list[str]:
    events: list[str] = []
    remaining = {"short": shortfalls}
    target = upload_module.UploadTarget("https://example.test", tmp_path / "agent.py", b"x", "h")
    pending = upload_module.PendingUpload("Agent", 0, "file-info", "public", "signature")
    client_context = MagicMock()
    client_context.__enter__.return_value = MagicMock()
    details = {
        "payment_method": "burn",
        "quote_id": "q",
        "amount_alpha_rao": amount_alpha_rao,
        "payment_netuid": 62,
        "expires_at": None,
        "price_usd": price_usd,
        "balance_alpha_rao": 0,
        "set_id": 1,
    }

    def _purchase(*_args, **_kwargs):
        if remaining["short"]:
            remaining["short"] -= 1
            events.append("purchase:short")
            return {
                "code": "insufficient_balance",
                "price_usd": short_price_usd,
                "price_alpha_rao": 2_296_000_000,
                "balance_alpha_rao": 2_200_000_000,
                "shortfall_alpha_rao": 96_000_000,
            }
        events.append("purchase")
        return None

    # The first read is the start-of-run price; later reads are the live price just before each purchase.
    prices = iter([price_usd] + [live_price_usd or price_usd] * 10)
    monkeypatch.setattr(payment_module, "_get_price", MagicMock(side_effect=lambda *a, **k: _market(next(prices))))
    monkeypatch.setattr(payment_module, "_get_balance", MagicMock(return_value=400_000_000))
    monkeypatch.setattr(payment_module, "_purchase_quote", _purchase)
    monkeypatch.setattr(payment_module.Confirm, "ask", staticmethod(lambda *a, **k: True))
    receipt = upload_module.PaymentReceipt(block_hash=CANON_HASH, extrinsic_index=7, quote_id="q")
    monkeypatch.setattr(
        upload_module, "_resolve_wallet_and_target", MagicMock(return_value=(_keypair_wallet(), target))
    )
    monkeypatch.setattr(upload_module.httpx, "Client", MagicMock(return_value=client_context))
    monkeypatch.setattr(upload_module, "_select_upload_competition", MagicMock(return_value=1))
    monkeypatch.setattr(upload_module, "_prepare_pending_upload", MagicMock(return_value=pending))
    monkeypatch.setattr(
        upload_module,
        "_resolve_openrouter_upload_credentials",
        MagicMock(return_value=upload_module.OpenRouterUploadCredentials("r", "m")),
    )
    monkeypatch.setattr(upload_module, "_print_upload_preview", MagicMock())
    requotes = {"n": 0}

    def _check(*_args, **_kwargs):
        # The first quote is `details`; every re-quote after a shortfall asks for a real burn.
        requotes["n"] += 1
        if requotes["n"] == 1:
            return details
        events.append("quote")
        return {**details, "quote_id": f"q{requotes['n']}", "amount_alpha_rao": 1, "balance_alpha_rao": 2_200_000_000}

    monkeypatch.setattr(upload_module, "_check_upload_allowed", _check)
    monkeypatch.setattr(payment_module, "_unlock_coldkey", MagicMock())
    confirm = MagicMock(side_effect=proceed) if isinstance(proceed, list) else MagicMock(return_value=proceed)
    monkeypatch.setattr(payment_module, "_confirm_payment", confirm)
    monkeypatch.setattr(payment_module, "_cancel_quote", MagicMock(side_effect=lambda *a, **k: events.append("cancel")))
    monkeypatch.setattr(
        payment_module, "_submit_eval_payment", MagicMock(side_effect=lambda **k: events.append("burn") or receipt)
    )
    monkeypatch.setattr(
        payment_module, "_confirm_burn", MagicMock(side_effect=lambda *a, **k: events.append("confirm"))
    )
    monkeypatch.setattr(
        upload_module, "_execute_upload", MagicMock(side_effect=lambda *a, **k: events.append("upload"))
    )
    return events


def test_upload_burn_confirms_then_purchases_before_uploading(monkeypatch, tmp_path: Path) -> None:
    events = _burn_upload_mocks(monkeypatch, tmp_path, proceed=True)
    result = CliRunner().invoke(upload_module.upload, [], obj={})
    assert result.exit_code == 0, result.output
    assert events == ["burn", "confirm", "purchase", "upload"]


def test_upload_tops_up_when_the_price_moved(monkeypatch, tmp_path: Path) -> None:
    events = _burn_upload_mocks(monkeypatch, tmp_path, proceed=True, shortfalls=1)
    result = CliRunner().invoke(upload_module.upload, [], obj={})
    assert result.exit_code == 0, result.output
    assert events == ["burn", "confirm", "purchase:short", "quote", "burn", "confirm", "purchase", "upload"]


def test_top_up_is_one_prompt_for_the_fresh_quote(monkeypatch, tmp_path: Path) -> None:
    _burn_upload_mocks(monkeypatch, tmp_path, proceed=True, shortfalls=1)
    monkeypatch.setattr(
        payment_module.Confirm, "ask", staticmethod(MagicMock(side_effect=AssertionError("no second prompt")))
    )
    result = CliRunner().invoke(upload_module.upload, [], obj={})
    assert result.exit_code == 0, result.output
    prompts = payment_module._confirm_payment.call_args_list
    assert [call.args[0]["quote_id"] for call in prompts] == ["q", "q2"], "the top-up prompt shows the fresh quote"
    assert [call.kwargs["first"] for call in prompts] == [True, False]


def test_declined_top_up_cancels_the_fresh_quote_and_keeps_the_balance(monkeypatch, tmp_path: Path) -> None:
    events = _burn_upload_mocks(monkeypatch, tmp_path, proceed=[True, False], shortfalls=1)
    result = CliRunner().invoke(upload_module.upload, [], obj={})
    assert result.exit_code == 0, result.output
    assert events == ["burn", "confirm", "purchase:short", "quote", "cancel"]
    assert "counts toward your next upload" in _flat(result.output)
    assert "burn balance" not in _flat(result.output), "the CLI talks about unused alpha, not an account"


@pytest.mark.parametrize(
    ("short_price_usd", "cause"),
    [(5.74, "Another upload was bought first"), (4.9, "alpha price fell")],
)
def test_shortfall_says_why_the_price_moved(monkeypatch, tmp_path: Path, short_price_usd: float, cause: str) -> None:
    _burn_upload_mocks(monkeypatch, tmp_path, proceed=True, shortfalls=1, short_price_usd=short_price_usd)
    result = CliRunner().invoke(upload_module.upload, [], obj={})
    assert result.exit_code == 0, result.output
    assert cause in _flat(result.output)


def test_upload_with_covering_balance_skips_the_burn(monkeypatch, tmp_path: Path) -> None:
    events = _burn_upload_mocks(monkeypatch, tmp_path, proceed=True, amount_alpha_rao=0)
    result = CliRunner().invoke(upload_module.upload, [], obj={})
    assert result.exit_code == 0, result.output
    assert events == ["purchase", "upload"]
    assert "q" in result.output, "the quote id is printed before a receipt-free purchase, for recovery"


def test_balance_only_purchase_asks_before_spending(monkeypatch, tmp_path: Path) -> None:
    events = _burn_upload_mocks(monkeypatch, tmp_path, proceed=True, amount_alpha_rao=0)
    monkeypatch.setattr(payment_module.Confirm, "ask", staticmethod(lambda *a, **k: False))
    result = CliRunner().invoke(upload_module.upload, [], obj={})
    assert result.exit_code == 0, result.output
    assert events == ["cancel"]


def test_zero_quote_shortfall_cancels_it_and_burns_on_a_fresh_quote(monkeypatch, tmp_path: Path) -> None:
    events = _burn_upload_mocks(monkeypatch, tmp_path, proceed=True, amount_alpha_rao=0, shortfalls=1)
    result = CliRunner().invoke(upload_module.upload, [], obj={})
    assert result.exit_code == 0, result.output
    assert events == ["purchase:short", "cancel", "quote", "burn", "confirm", "purchase", "upload"]


def test_upload_declined_burn_cancels_the_quote(monkeypatch, tmp_path: Path) -> None:
    events = _burn_upload_mocks(monkeypatch, tmp_path, proceed=False)
    result = CliRunner().invoke(upload_module.upload, [], obj={})
    assert result.exit_code == 0, result.output
    assert events == ["cancel"]


def test_upload_explains_pricing_with_the_live_settings(monkeypatch, tmp_path: Path) -> None:
    _burn_upload_mocks(monkeypatch, tmp_path, proceed=True)
    result = CliRunner().invoke(upload_module.upload, [], obj={})
    assert result.exit_code == 0, result.output
    assert "raises it 15%" in _flat(result.output) and "30 min" in _flat(result.output)
    assert "ridges upload --help" in result.output


def test_upload_help_explains_pricing_and_auto_approval() -> None:
    result = CliRunner().invoke(upload_module.upload, ["--help"], obj={})
    assert result.exit_code == 0, result.output
    assert "How upload pricing works" in result.output
    assert "--max-price" in result.output and "--yes" in result.output
    assert "burn balance" not in _flat(result.output)


def test_max_price_approves_burns_at_or_below_the_limit(monkeypatch, tmp_path: Path) -> None:
    events = _burn_upload_mocks(monkeypatch, tmp_path, proceed=False)
    result = CliRunner().invoke(upload_module.upload, ["--max-price", "50"], obj={})
    assert result.exit_code == 0, result.output
    assert events == ["burn", "confirm", "purchase", "upload"]
    payment_module._confirm_payment.assert_not_called()
    assert "Price limit $50.00" in _flat(result.output) and "auto-approved" in _flat(result.output)


def test_max_price_stops_before_burning_above_the_limit(monkeypatch, tmp_path: Path) -> None:
    events = _burn_upload_mocks(monkeypatch, tmp_path, proceed=True, price_usd=60.0)
    result = CliRunner().invoke(upload_module.upload, ["--max-price", "50"], obj={})
    assert result.exit_code == 0, result.output
    assert events == ["cancel"]
    assert "above your $50.00 limit" in _flat(result.output)
    assert "~8 min" in _flat(result.output), "60 -> 50 at a 30-minute half-life takes about 8 minutes"


def test_max_price_rechecks_the_live_price_before_buying(monkeypatch, tmp_path: Path) -> None:
    events = _burn_upload_mocks(monkeypatch, tmp_path, proceed=True, live_price_usd=60.0)
    result = CliRunner().invoke(upload_module.upload, ["--max-price", "50"], obj={})
    assert result.exit_code == 0, result.output
    assert events == ["burn", "confirm"], "burned alpha stays as balance; nothing is bought above the limit"
    assert "above your $50.00 limit" in _flat(result.output)
    assert "counts toward your next upload" in _flat(result.output)


def test_yes_approves_without_a_limit(monkeypatch, tmp_path: Path) -> None:
    events = _burn_upload_mocks(monkeypatch, tmp_path, proceed=False, amount_alpha_rao=0)
    monkeypatch.setattr(payment_module.Confirm, "ask", staticmethod(MagicMock(side_effect=AssertionError("no prompt"))))
    result = CliRunner().invoke(upload_module.upload, ["--yes"], obj={})
    assert result.exit_code == 0, result.output
    assert events == ["purchase", "upload"]
    assert "No price limit" in _flat(result.output)


@pytest.mark.parametrize("flag", [["--yes"], ["--max-price", "50"]])
def test_auto_approval_flags_do_not_apply_to_credit_uploads(flag: list[str]) -> None:
    result = CliRunner().invoke(upload_module.upload, ["--use-credit", *flag], obj={})
    assert result.exit_code != 0
    assert "--use-credit" in result.output


def test_upload_hands_the_purchase_summary_to_the_receipt(monkeypatch, tmp_path: Path) -> None:
    _burn_upload_mocks(monkeypatch, tmp_path, proceed=True, live_price_usd=5.5)
    execute = MagicMock()
    monkeypatch.setattr(upload_module, "_execute_upload", execute)
    result = CliRunner().invoke(upload_module.upload, [], obj={})
    assert result.exit_code == 0, result.output
    summary = execute.call_args.kwargs["purchase"]
    assert (summary.quoted_usd, summary.paid_usd, summary.burns, summary.balance_alpha_rao) == (
        5.0,
        5.5,
        1,
        400_000_000,
    )
    assert summary.receipt.quote_id == "q"


def test_receipt_panel_lists_price_burn_and_balance(monkeypatch) -> None:
    print_result = MagicMock()
    monkeypatch.setattr(upload_module.console, "print", print_result)
    summary = upload_module.PurchaseSummary(
        receipt=upload_module.PaymentReceipt(block_hash=CANON_HASH, extrinsic_index=7, quote_id="q"),
        quoted_usd=8.47,
        paid_usd=10.39,
        burned_alpha_rao=13_730_100_000,
        burns=2,
        balance_alpha_rao=399_800_000,
    )
    upload_module._handle_upload_result(_FakeResponse(200, json_data={"message": "done"}), name="a", purchase=summary)
    panel = print_result.call_args.args[0].renderable
    assert "done" in panel and "quoted $8.47" in panel and "13.7301 α in 2 burns" in panel
    left_line = next(line for line in panel.splitlines() if line.startswith("Left over"))
    assert "0.3998 α" in left_line and "$" not in left_line, "unused alpha is alpha; it has no dollar figure"


def _recorded(monkeypatch):
    import io

    from rich.console import Console

    recording = Console(record=True, file=io.StringIO(), width=100)
    monkeypatch.setattr(payment_module, "console", recording)
    return recording


def test_price_summary_shows_the_balance_in_alpha_only(monkeypatch) -> None:
    recording = _recorded(monkeypatch)
    details = {"price_usd": 5.0, "balance_alpha_rao": 634_500_000, "amount_alpha_rao": 7_217_123_912}
    payment_module._print_price_summary(details, _market())
    lines = recording.export_text().splitlines()
    balance_line = next(line for line in lines if "Unused burn" in line)
    assert "0.6345 α" in balance_line and "$" not in balance_line
    assert "price + buffer, minus unused burn" in next(line for line in lines if "To burn" in line)


def test_pricing_intro_is_three_dim_bullets(monkeypatch) -> None:
    recording = _recorded(monkeypatch)
    payment_module._print_pricing_intro(_market(), help_command="ridges upload")
    lines = [line.strip() for line in recording.export_text().splitlines() if line.strip()]
    assert [line[0] for line in lines[:3]] == ["•", "•", "•"]
    assert lines[3] == "Full rules: ridges upload --help"


@pytest.mark.parametrize("first", [True, False])
def test_burn_prompt_defaults_to_yes(monkeypatch, first: bool) -> None:
    ask = MagicMock(return_value="y")
    monkeypatch.setattr(upload_module.Prompt, "ask", ask)
    details = {"amount_alpha_rao": 7_217_123_912, "payment_netuid": 332}
    assert payment_module._confirm_payment(details, first=first, market=_market())
    assert ask.call_args.kwargs["default"] == "y"


def test_resume_commands_are_printed_without_wrapping(monkeypatch) -> None:
    print_result = MagicMock()
    monkeypatch.setattr(upload_module.console, "print", print_result)
    payment_module._print_resume_hint(
        "ridges resume-upload --quote-id q --payment-block-hash 0xab --payment-extrinsic-index 5"
    )
    command_prints = [call for call in print_result.call_args_list if "resume-upload" in str(call)]
    assert command_prints and all(call.kwargs.get("soft_wrap") for call in command_prints)


def test_get_price_derives_the_alpha_rate() -> None:
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "price_usd": 5.0,
        "amount_alpha_rao": 11_000_000_000,
        "floor_usd": 5.0,
        "half_life_minutes": 30.0,
        "multiplier": 1.148698354997035,
    }
    client = MagicMock()
    client.get.return_value = response
    market = payment_module._get_price(client, api_url="https://x", set_id=29)
    assert client.get.call_args.kwargs["params"] == {"set_id": 29}
    assert market.alpha_price_usd == pytest.approx(0.5), "11 alpha with the 10% buffer buys $5, so alpha is $0.50"


def test_resume_upload_confirms_before_competition_selection(monkeypatch, tmp_path: Path) -> None:
    target = upload_module.UploadTarget("https://example.test", tmp_path / "agent.py", b"x", "h")
    client_context = MagicMock()
    client_context.__enter__.return_value = MagicMock()
    confirm = MagicMock()
    monkeypatch.setattr(upload_module, "_resolve_wallet", MagicMock(return_value=_keypair_wallet()))
    monkeypatch.setattr(upload_module, "_resolve_target", MagicMock(return_value=target))
    monkeypatch.setattr(upload_module.httpx, "Client", MagicMock(return_value=client_context))
    monkeypatch.setattr(
        upload_module,
        "_select_upload_competition",
        MagicMock(side_effect=upload_module.click.ClickException("Competition 17 is not accepting uploads")),
    )
    monkeypatch.setattr(upload_module, "_confirm_burn", confirm)
    monkeypatch.setattr(upload_module, "_purchase_quote", MagicMock(return_value=None))

    result = CliRunner().invoke(
        upload_module.resume_upload,
        ["--quote-id", "quote", "--payment-block-hash", CANON_HASH, "--payment-extrinsic-index", "3"],
        obj={},
    )

    assert result.exit_code != 0
    assert confirm.call_args.kwargs["receipt"] == upload_module.PaymentReceipt(
        block_hash=CANON_HASH, extrinsic_index=3, quote_id="quote"
    )


def test_resume_upload_confirms_before_reading_the_agent_file(monkeypatch, tmp_path: Path) -> None:
    import bittensor_wallet.wallet as wallet_module

    monkeypatch.setattr(wallet_module, "Wallet", MagicMock(return_value=_keypair_wallet()))
    confirm = MagicMock()
    monkeypatch.setattr(upload_module, "_confirm_burn", confirm)
    monkeypatch.setattr(upload_module, "_purchase_quote", MagicMock(return_value=None))

    result = CliRunner().invoke(
        upload_module.resume_upload,
        [
            "--coldkey-name",
            "c",
            "--hotkey-name",
            "h",
            "--file",
            str(tmp_path / "missing" / "agent.py"),
            "--quote-id",
            "quote",
            "--payment-block-hash",
            CANON_HASH,
            "--payment-extrinsic-index",
            "3",
        ],
        obj={},
    )

    assert result.exit_code != 0
    assert confirm.call_args.kwargs["receipt"] == upload_module.PaymentReceipt(
        block_hash=CANON_HASH, extrinsic_index=3, quote_id="quote"
    )


def test_purchase_quote_posts_signed_request_and_returns_shortfall() -> None:
    client = _FakeClient(_FakeResponse(200, json_data={"status": "purchased"}))
    assert upload_module._purchase_quote(client, api_url="https://x", wallet=_keypair_wallet(), quote_id="q") is None
    body = client.calls[0]["json"]
    assert client.calls[0]["url"] == "https://x/upload/quote/q/purchase"
    assert KEYPAIR.verify(purchase_signing_string(KEYPAIR.ss58_address, "q"), bytes.fromhex(body["signature"]))

    detail = {
        "code": "insufficient_balance",
        "price_usd": 5.74,
        "price_alpha_rao": 2_296_000_000,
        "balance_alpha_rao": 2_200_000_000,
        "shortfall_alpha_rao": 96_000_000,
    }
    client = _FakeClient(_FakeResponse(402, json_data={"detail": detail}))
    assert upload_module._purchase_quote(client, api_url="https://x", wallet=_keypair_wallet(), quote_id="q") == detail

    client = _FakeClient(_FakeResponse(409, text="quote_cancelled"))
    with pytest.raises(upload_module.click.ClickException, match="quote_cancelled"):
        upload_module._purchase_quote(client, api_url="https://x", wallet=_keypair_wallet(), quote_id="q")


def test_resume_upload_refuses_when_balance_is_short(monkeypatch, tmp_path: Path) -> None:
    target = upload_module.UploadTarget("https://example.test", tmp_path / "agent.py", b"x", "h")
    client_context = MagicMock()
    client_context.__enter__.return_value = MagicMock()
    monkeypatch.setattr(upload_module, "_resolve_wallet", MagicMock(return_value=_keypair_wallet()))
    monkeypatch.setattr(upload_module, "_resolve_target", MagicMock(return_value=target))
    monkeypatch.setattr(upload_module.httpx, "Client", MagicMock(return_value=client_context))
    monkeypatch.setattr(upload_module, "_confirm_burn", MagicMock())
    monkeypatch.setattr(
        upload_module,
        "_purchase_quote",
        MagicMock(
            return_value={
                "code": "insufficient_balance",
                "price_usd": 6,
                "price_alpha_rao": 2_400_000_000,
                "balance_alpha_rao": 2_200_000_000,
                "shortfall_alpha_rao": 200_000_000,
            }
        ),
    )
    execute = MagicMock()
    monkeypatch.setattr(upload_module, "_execute_upload", execute)

    result = CliRunner().invoke(
        upload_module.resume_upload,
        ["--quote-id", "quote", "--payment-block-hash", CANON_HASH, "--payment-extrinsic-index", "3"],
        obj={},
    )

    assert result.exit_code != 0
    assert "ridges upload" in result.output
    execute.assert_not_called()


def test_upload_payload_omits_missing_receipt() -> None:
    pending = upload_module.PendingUpload("Agent", 0, "file-info", "public", "signature")
    receipt = upload_module.PaymentReceipt(block_hash=None, extrinsic_index=None, quote_id="q")
    payload = upload_module._upload_payload(
        pending=pending, receipt=receipt, credentials=upload_module.OpenRouterUploadCredentials("r", "m"), set_id=1
    )
    assert payload["quote_id"] == "q"
    assert "payment_block_hash" not in payload and "payment_extrinsic_index" not in payload


def test_balance_command_prints_the_coldkey_balance(monkeypatch) -> None:
    import bittensor_wallet.wallet as wallet_module

    monkeypatch.setattr(wallet_module, "Wallet", MagicMock(return_value=_keypair_wallet()))
    response = MagicMock(status_code=200)
    response.json.return_value = {"coldkey": "ck", "balance_alpha_rao": 12_500_000_000}
    client = MagicMock()
    client.get.return_value = response
    client_context = MagicMock()
    client_context.__enter__.return_value = client
    monkeypatch.setattr(upload_module.httpx, "Client", MagicMock(return_value=client_context))

    result = CliRunner().invoke(upload_module.balance, ["--coldkey-name", "c", "--hotkey-name", "h"], obj={})

    assert result.exit_code == 0, result.output
    assert "Unused burn" in result.output and "12.5000 alpha" in result.output
    assert client.get.call_args.args[0].endswith("/upload/balance")


def test_resume_upload_with_quote_only_skips_confirm_and_purchases(monkeypatch, tmp_path: Path) -> None:
    target = upload_module.UploadTarget("https://example.test", tmp_path / "agent.py", b"x", "h")
    client_context = MagicMock()
    client_context.__enter__.return_value = MagicMock()
    monkeypatch.setattr(upload_module, "_resolve_wallet", MagicMock(return_value=_keypair_wallet()))
    monkeypatch.setattr(upload_module, "_resolve_target", MagicMock(return_value=target))
    monkeypatch.setattr(upload_module.httpx, "Client", MagicMock(return_value=client_context))
    monkeypatch.setattr(upload_module, "_select_upload_competition", MagicMock(return_value=1))
    monkeypatch.setattr(upload_module, "_prepare_pending_upload", MagicMock())
    monkeypatch.setattr(upload_module, "_resolve_openrouter_upload_credentials", MagicMock())
    monkeypatch.setattr(upload_module, "_print_upload_preview", MagicMock())
    confirm = MagicMock(side_effect=AssertionError("nothing was burned, so nothing to confirm"))
    monkeypatch.setattr(upload_module, "_confirm_burn", confirm)
    purchase = MagicMock(return_value=None)
    monkeypatch.setattr(upload_module, "_purchase_quote", purchase)
    execute = MagicMock()
    monkeypatch.setattr(upload_module, "_execute_upload", execute)

    result = CliRunner().invoke(upload_module.resume_upload, ["--quote-id", "quote"], obj={})

    assert result.exit_code == 0, result.output
    purchase.assert_called_once()
    assert execute.call_args.kwargs["receipt"] == upload_module.PaymentReceipt(
        block_hash=None, extrinsic_index=None, quote_id="quote"
    )
