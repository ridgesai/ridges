from __future__ import annotations

from unittest.mock import MagicMock

from bittensor_wallet.keypair import Keypair
from click.testing import CliRunner

import miners.cli.commands.prepare_upload as prepare_module
import miners.cli.commands.upload as upload_module
from miners.cli.commands.upload import PaymentReceipt
from utils.upload_ticket import FUNDING_BURN, FUNDING_CREDIT, decode_ticket, verify_ticket_signature

KEYPAIR = Keypair.create_from_seed("0x" + "ab" * 32)
QUOTE_ID = "2f3b0000-0000-4000-8000-000000000001"
CREDIT_ID = "3a4c0000-0000-4000-8000-000000000002"


def _fake_wallet() -> MagicMock:
    wallet = MagicMock()
    wallet.hotkey = KEYPAIR  # real keypair: ss58_address, public_key, sign all real
    return wallet


def _extract_ticket(output: str) -> str:
    # The ticket is printed as the last non-empty line.
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    for line in reversed(lines):
        try:
            decode_ticket(line)
            return line
        except ValueError:
            continue
    raise AssertionError(f"No decodable ticket in output:\n{output}")


def _prepare_response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = payload
    return response


def test_burn_mode_prints_verified_ticket(monkeypatch):
    call_order: list[str] = []

    def _fake_unlock(wallet):
        call_order.append("unlock")

    def _fake_confirm(details):
        call_order.append("confirm")
        return True

    def _fake_submit(*, wallet, payment_method_details):
        call_order.append("submit")
        return PaymentReceipt(block_hash="0x87d2", extrinsic_index=7, quote_id=QUOTE_ID)

    monkeypatch.setattr(prepare_module, "_resolve_wallet", lambda coldkey_name, hotkey_name: _fake_wallet())
    monkeypatch.setattr(prepare_module, "_unlock_coldkey", MagicMock(side_effect=_fake_unlock))
    monkeypatch.setattr(prepare_module, "_confirm_payment", MagicMock(side_effect=_fake_confirm))
    monkeypatch.setattr(prepare_module, "_submit_eval_payment", MagicMock(side_effect=_fake_submit))
    client = MagicMock()
    client.post.return_value = _prepare_response(
        {
            "payment_method": "burn",
            "quote_id": QUOTE_ID,
            "amount_alpha_rao": 123,
            "payment_netuid": 62,
            "expires_at": None,
        }
    )
    monkeypatch.setattr(
        prepare_module.httpx,
        "Client",
        MagicMock(
            return_value=MagicMock(__enter__=MagicMock(return_value=client), __exit__=MagicMock(return_value=False))
        ),
    )

    result = CliRunner().invoke(
        prepare_module.prepare_upload, ["--coldkey-name", "c", "--hotkey-name", "h"], obj={"url": None}
    )

    assert result.exit_code == 0, result.output
    assert call_order == ["unlock", "confirm", "submit"]
    ticket = decode_ticket(_extract_ticket(result.output))
    assert ticket.funding == FUNDING_BURN
    assert ticket.hotkey == KEYPAIR.ss58_address
    assert ticket.quote_id == QUOTE_ID
    assert ticket.payment_block_hash == "0x87d2"
    assert ticket.payment_extrinsic_index == 7
    assert verify_ticket_signature(ticket)
    assert "bearer" in result.output.lower() or "password" in result.output.lower()


def test_burn_submission_failure_prints_recoverable_quote_id(monkeypatch):
    """If `_submit_eval_payment` raises after broadcast (e.g. an RPC drop while waiting for
    finalization), the burn may have already landed — the quote id must survive so the miner
    can recover with `prepare-upload --quote-id` instead of burning a second time."""
    monkeypatch.setattr(prepare_module, "_resolve_wallet", lambda coldkey_name, hotkey_name: _fake_wallet())
    monkeypatch.setattr(prepare_module, "_unlock_coldkey", MagicMock())
    monkeypatch.setattr(prepare_module, "_confirm_payment", MagicMock(return_value=True))
    monkeypatch.setattr(prepare_module, "_submit_eval_payment", MagicMock(side_effect=RuntimeError("rpc dropped")))
    client = MagicMock()
    client.post.return_value = _prepare_response(
        {
            "payment_method": "burn",
            "quote_id": QUOTE_ID,
            "amount_alpha_rao": 123,
            "payment_netuid": 62,
            "expires_at": None,
        }
    )
    monkeypatch.setattr(
        prepare_module.httpx,
        "Client",
        MagicMock(
            return_value=MagicMock(__enter__=MagicMock(return_value=client), __exit__=MagicMock(return_value=False))
        ),
    )

    result = CliRunner().invoke(
        prepare_module.prepare_upload, ["--coldkey-name", "c", "--hotkey-name", "h"], obj={"url": None}
    )

    assert result.exit_code != 0
    assert QUOTE_ID in result.output
    assert "prepare-upload --quote-id" in result.output


def test_burn_submission_keyboard_interrupt_prints_recoverable_quote_id(monkeypatch):
    """A Ctrl-C while wait_for_finalization blocks raises KeyboardInterrupt, not Exception — the
    most likely real interruption during a burn. The recovery handler must catch BaseException so
    it still prints recovery info instead of silently stranding an already-broadcast burn.

    This CLI runs on rich_click (see miners/cli/click_ext.py: `import rich_click as click`).
    RichCommand.main() unconditionally converts an uncaught KeyboardInterrupt/EOFError into a
    `click.exceptions.Abort() from None` before CliRunner ever sees it — verified empirically
    against the installed rich_click/click 8.3.0: this holds for both standalone_mode=True and
    False, and the explicit `from None` means `result.exception.__cause__` is always None too, so
    neither `isinstance(result.exception, KeyboardInterrupt)` nor a `__cause__` check can ever pass.
    `__context__` is unaffected by `from None` (only `__cause__`/`__suppress_context__` are), so it
    still reliably holds the original KeyboardInterrupt — that's what proves the interrupt really
    propagated through our handler instead of being swallowed. standalone_mode=False avoids the
    "Aborted!"/SystemExit wrapping that standalone mode would otherwise add on top.
    """
    import click

    monkeypatch.setattr(prepare_module, "_resolve_wallet", lambda coldkey_name, hotkey_name: _fake_wallet())
    monkeypatch.setattr(prepare_module, "_unlock_coldkey", MagicMock())
    monkeypatch.setattr(prepare_module, "_confirm_payment", MagicMock(return_value=True))
    monkeypatch.setattr(prepare_module, "_submit_eval_payment", MagicMock(side_effect=KeyboardInterrupt))
    client = MagicMock()
    client.post.return_value = _prepare_response(
        {
            "payment_method": "burn",
            "quote_id": QUOTE_ID,
            "amount_alpha_rao": 123,
            "payment_netuid": 62,
            "expires_at": None,
        }
    )
    monkeypatch.setattr(
        prepare_module.httpx,
        "Client",
        MagicMock(
            return_value=MagicMock(__enter__=MagicMock(return_value=client), __exit__=MagicMock(return_value=False))
        ),
    )

    result = CliRunner().invoke(
        prepare_module.prepare_upload,
        ["--coldkey-name", "c", "--hotkey-name", "h"],
        obj={"url": None},
        standalone_mode=False,
    )

    assert result.exit_code != 0
    assert QUOTE_ID in result.output
    assert "prepare-upload --quote-id" in result.output
    assert isinstance(result.exception, click.exceptions.Abort)
    assert isinstance(result.exception.__context__, KeyboardInterrupt)


def test_credit_mode_never_touches_coldkey(monkeypatch):
    monkeypatch.setattr(prepare_module, "_resolve_wallet", lambda coldkey_name, hotkey_name: _fake_wallet())
    unlock = MagicMock(side_effect=AssertionError("credit mode must not unlock the coldkey"))
    monkeypatch.setattr(prepare_module, "_unlock_coldkey", unlock)
    submit = MagicMock(side_effect=AssertionError("credit mode must not burn"))
    monkeypatch.setattr(prepare_module, "_submit_eval_payment", submit)
    client = MagicMock()
    client.post.return_value = _prepare_response(
        {"payment_method": "credit", "credit_id": CREDIT_ID, "amount_alpha_rao": 0}
    )
    monkeypatch.setattr(
        prepare_module.httpx,
        "Client",
        MagicMock(
            return_value=MagicMock(__enter__=MagicMock(return_value=client), __exit__=MagicMock(return_value=False))
        ),
    )

    result = CliRunner().invoke(
        prepare_module.prepare_upload, ["--coldkey-name", "c", "--hotkey-name", "h", "--use-credit"], obj={"url": None}
    )

    assert result.exit_code == 0, result.output
    ticket = decode_ticket(_extract_ticket(result.output))
    assert ticket.funding == FUNDING_CREDIT
    assert ticket.credit_id == CREDIT_ID
    assert verify_ticket_signature(ticket)


def test_resume_mode_mints_burn_ticket_without_network(monkeypatch):
    monkeypatch.setattr(prepare_module, "_resolve_wallet", lambda coldkey_name, hotkey_name: _fake_wallet())
    submit = MagicMock(side_effect=AssertionError("resume mode must not burn again"))
    monkeypatch.setattr(prepare_module, "_submit_eval_payment", submit)

    result = CliRunner().invoke(
        prepare_module.prepare_upload,
        [
            "--coldkey-name",
            "c",
            "--hotkey-name",
            "h",
            "--quote-id",
            QUOTE_ID,
            "--payment-block-hash",
            "0x87d2",
            "--payment-extrinsic-index",
            "7",
        ],
        obj={"url": None},
    )

    assert result.exit_code == 0, result.output
    ticket = decode_ticket(_extract_ticket(result.output))
    assert ticket.funding == FUNDING_BURN
    assert ticket.quote_id == QUOTE_ID
    assert verify_ticket_signature(ticket)


def test_upload_failure_prints_ticket(monkeypatch):
    """ridges upload's post-payment failure path must print a v2 ticket for web recovery."""
    wallet = _fake_wallet()
    failed = MagicMock()
    failed.status_code = 500
    failed.headers = {"content-type": "application/json"}
    failed.json.return_value = {"detail": "boom"}
    monkeypatch.setattr(upload_module, "_submit_upload", MagicMock(return_value=failed))

    import click
    import pytest

    target = MagicMock()
    pending = upload_module.PendingUpload(
        name="agent", version_num=0, file_info="fi", public_key=KEYPAIR.public_key.hex(), signature="sig"
    )
    receipt = PaymentReceipt(block_hash="0x87d2", extrinsic_index=7, quote_id=QUOTE_ID)
    printed: list[str] = []
    monkeypatch.setattr(upload_module.console, "print", lambda *a, **k: printed.append(str(a[0]) if a else ""))

    with pytest.raises(click.ClickException):
        upload_module._execute_upload(
            MagicMock(),
            wallet=wallet,
            target=target,
            credentials=upload_module.OpenRouterUploadCredentials(runtime_api_key="r", management_key="m"),
            receipt=receipt,
            pending=pending,
            run_check=False,
            emit_ticket_on_failure=True,
        )

    joined = "\n".join(printed)
    ticket = decode_ticket(_extract_ticket(joined))
    assert ticket.funding == FUNDING_BURN
    assert ticket.quote_id == QUOTE_ID
    assert ticket.payment_block_hash == "0x87d2"
    assert ticket.payment_extrinsic_index == 7
    assert verify_ticket_signature(ticket)


def test_upload_transport_failure_prints_ticket(monkeypatch):
    """A timeout/disconnect after payment must also print a recovery ticket (spec §4)."""
    import httpx
    import pytest

    wallet = _fake_wallet()
    monkeypatch.setattr(upload_module, "_submit_upload", MagicMock(side_effect=httpx.ConnectError("network down")))
    pending = upload_module.PendingUpload(
        name="agent", version_num=0, file_info="fi", public_key=KEYPAIR.public_key.hex(), signature="sig"
    )
    receipt = PaymentReceipt(block_hash="0x87d2", extrinsic_index=7, quote_id=QUOTE_ID)
    printed: list[str] = []
    monkeypatch.setattr(upload_module.console, "print", lambda *a, **k: printed.append(str(a[0]) if a else ""))

    with pytest.raises(httpx.ConnectError):
        upload_module._execute_upload(
            MagicMock(),
            wallet=wallet,
            target=MagicMock(),
            credentials=upload_module.OpenRouterUploadCredentials(runtime_api_key="r", management_key="m"),
            receipt=receipt,
            pending=pending,
            run_check=False,
            emit_ticket_on_failure=True,
        )

    ticket = decode_ticket(_extract_ticket("\n".join(printed)))
    assert ticket.funding == FUNDING_BURN
    assert ticket.quote_id == QUOTE_ID
    assert ticket.payment_block_hash == "0x87d2"
    assert ticket.payment_extrinsic_index == 7
    assert verify_ticket_signature(ticket)


def test_upload_failure_with_credit_receipt_prints_credit_ticket(monkeypatch):
    """A failure after paying with a credit must print a credit ticket, not a burn ticket."""
    import click
    import pytest

    wallet = _fake_wallet()
    failed = MagicMock()
    failed.status_code = 500
    failed.headers = {"content-type": "application/json"}
    failed.json.return_value = {"detail": "boom"}
    monkeypatch.setattr(upload_module, "_submit_upload", MagicMock(return_value=failed))

    pending = upload_module.PendingUpload(
        name="agent", version_num=0, file_info="fi", public_key=KEYPAIR.public_key.hex(), signature="sig"
    )
    receipt = upload_module.CreditReceipt(credit_id=CREDIT_ID)
    printed: list[str] = []
    monkeypatch.setattr(upload_module.console, "print", lambda *a, **k: printed.append(str(a[0]) if a else ""))

    with pytest.raises(click.ClickException):
        upload_module._execute_upload(
            MagicMock(),
            wallet=wallet,
            target=MagicMock(),
            credentials=upload_module.OpenRouterUploadCredentials(runtime_api_key="r", management_key="m"),
            receipt=receipt,
            pending=pending,
            run_check=False,
            emit_ticket_on_failure=True,
        )

    ticket = decode_ticket(_extract_ticket("\n".join(printed)))
    assert ticket.funding == FUNDING_CREDIT
    assert ticket.credit_id == CREDIT_ID
    assert verify_ticket_signature(ticket)


def test_prepare_upload_remains_competition_free() -> None:
    result = CliRunner().invoke(
        prepare_module.prepare_upload,
        ["--competition", "7"],
        obj={"url": None},
    )

    assert result.exit_code != 0
    assert "No such option" in result.output
    assert "--competition" in result.output
