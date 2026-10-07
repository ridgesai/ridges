"""Upload command for the top-level Ridges CLI."""

from __future__ import annotations

import hashlib
import os
import sys
import time
import uuid as _uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Confirm, Prompt

from miners.cli.click_ext import click, format_help
from utils.burn_receipt import canonical_receipt
from utils.upload_ticket import (
    FUNDING_BURN,
    FUNDING_CREDIT,
    UploadTicket,
    cancel_signing_string,
    confirm_signing_string,
    encode_ticket,
    purchase_signing_string,
    sign_ticket,
)

console = Console()
DEFAULT_API_BASE_URL = "https://agent-upload.ridges.ai"
UPLOAD_TIMEOUT_SECONDS = 120
MAX_AGENT_FILE_SIZE_BYTES = 2 * 1024 * 1024
PRICING_VERSION = 2
MIN_QUOTE_SECONDS_BEFORE_BURN = 180

if TYPE_CHECKING:
    from bittensor_wallet.wallet import Wallet


@dataclass(frozen=True, slots=True)
class UploadTarget:
    api_url: str
    agent_path: Path
    file_content: bytes
    content_hash: str


@dataclass(frozen=True, slots=True)
class PendingUpload:
    name: str
    version_num: int
    file_info: str
    public_key: str
    signature: str


@dataclass(frozen=True, slots=True)
class PaymentReceipt:
    """A burn receipt. Both receipt fields are None when the burn balance covered the quote and nothing was burned."""

    block_hash: Optional[str]
    extrinsic_index: Optional[int]
    quote_id: Optional[str] = None


@dataclass(frozen=True, slots=True)
class CreditReceipt:
    credit_id: str


@dataclass(frozen=True, slots=True)
class OpenRouterUploadCredentials:
    runtime_api_key: str
    management_key: str


def get_or_prompt(key: str, prompt: str, default: Optional[str] = None) -> str:
    """Get a value from env or ask interactively."""
    value = os.getenv(key)
    if not value:
        value = Prompt.ask(f"🎯 {prompt}", default=default) if default else Prompt.ask(f"🎯 {prompt}")
    return value


def get_secret_or_prompt(key: str, prompt: str) -> str:
    """Get a secret from env or ask interactively without echoing input."""
    value = (os.getenv(key) or "").strip()
    if not value:
        value = Prompt.ask(f"🔐 {prompt}", password=True).strip()
    return value


def _resolve_agent_file(path_str: str) -> Path:
    agent_path = Path(path_str).expanduser()
    if not agent_path.exists() or not agent_path.is_file() or agent_path.name != "agent.py":
        raise click.ClickException("File must be named 'agent.py' and exist")
    return agent_path


def _read_upload_target(api_url: str, path_str: str) -> UploadTarget:
    agent_path = _resolve_agent_file(path_str)
    file_size = agent_path.stat().st_size
    if file_size > MAX_AGENT_FILE_SIZE_BYTES:
        raise click.ClickException("Agent file must not exceed 2MB")

    file_content = agent_path.read_bytes()
    return UploadTarget(
        api_url=api_url,
        agent_path=agent_path,
        file_content=file_content,
        content_hash=hashlib.sha256(file_content).hexdigest(),
    )


def _print_upload_preview(*, hotkey: str, target: UploadTarget) -> None:
    console.print(
        Panel(
            f"[bold cyan]Uploading Agent[/bold cyan]\n"
            f"[yellow]Hotkey:[/yellow] {hotkey}\n"
            f"[yellow]File:[/yellow] {target.agent_path}\n"
            f"[yellow]API:[/yellow] {target.api_url}",
            title="Upload",
            border_style="cyan",
        )
    )


def _get_upload_competitions(client: httpx.Client, *, api_url: str) -> list[dict]:
    response = client.get(f"{api_url}/competitions?accepting=true", timeout=UPLOAD_TIMEOUT_SECONDS)
    if response.status_code != 200:
        raise click.ClickException(f"Could not discover upload competitions: {response.text}")

    competitions = response.json()
    if not isinstance(competitions, list):
        raise click.ClickException("Server returned an invalid upload competition list")
    return competitions


def _select_upload_competition(
    client: httpx.Client,
    *,
    api_url: str,
    requested_set_id: int | None,
) -> int:
    competitions = _get_upload_competitions(client, api_url=api_url)
    choices = {competition.get("set_id"): competition for competition in competitions}
    if requested_set_id is not None:
        if requested_set_id not in choices:
            raise click.ClickException(f"Competition {requested_set_id} is not accepting uploads")
        return requested_set_id

    if not competitions:
        raise click.ClickException("No competition is accepting uploads")

    rendered = ", ".join(
        f"{competition['set_id']} ({competition.get('name') or 'unnamed'})" for competition in competitions
    )
    if not sys.stdin.isatty():
        raise click.ClickException(
            f"Competitions accepting uploads: {rendered}. Use --competition INTEGER to choose one."
        )

    console.print(f"Competitions accepting uploads: {rendered}")
    selection = Prompt.ask("🎯 Competition to upload to", choices=[str(set_id) for set_id in choices])
    return int(selection)


def _lookup_latest_agent(
    client: httpx.Client,
    *,
    api_url: str,
    hotkey: str,
    set_id: int,
) -> dict | None:
    response = client.get(
        f"{api_url}/retrieval/all-agents-by-hotkey?miner_hotkey={hotkey}",
        timeout=UPLOAD_TIMEOUT_SECONDS,
    )
    if response.status_code == 200 and isinstance(response.json(), list):
        matching = [agent for agent in response.json() if agent.get("set_id") == set_id]
        if matching:
            return max(matching, key=lambda agent: agent.get("version_num", -1))
    return None


def _resolve_upload_name_and_version(
    client: httpx.Client,
    *,
    api_url: str,
    hotkey: str,
    set_id: int,
) -> tuple[str, int]:
    latest_agent = _lookup_latest_agent(client, api_url=api_url, hotkey=hotkey, set_id=set_id)
    if latest_agent:
        return latest_agent.get("name"), latest_agent.get("version_num", -1) + 1
    return Prompt.ask("Enter a name for your miner agent"), 0


def _build_pending_upload(*, wallet, name: str, version_num: int, content_hash: str) -> PendingUpload:
    public_key = wallet.hotkey.public_key.hex()
    file_info = f"{wallet.hotkey.ss58_address}:{content_hash}:{version_num}"
    signature = wallet.hotkey.sign(file_info).hex()
    return PendingUpload(
        name=name,
        version_num=version_num,
        file_info=file_info,
        public_key=public_key,
        signature=signature,
    )


def _resolve_openrouter_upload_credentials(
    *,
    openrouter_api_key: Optional[str],
    openrouter_management_key: Optional[str],
) -> OpenRouterUploadCredentials:
    runtime_api_key = (openrouter_api_key or "").strip() or get_secret_or_prompt(
        "RIDGES_OPENROUTER_API_KEY",
        "Enter your OpenRouter runtime API key",
    )
    management_key = (openrouter_management_key or "").strip() or get_secret_or_prompt(
        "RIDGES_OPENROUTER_MANAGEMENT_KEY",
        "Enter your OpenRouter management key",
    )
    return OpenRouterUploadCredentials(
        runtime_api_key=runtime_api_key,
        management_key=management_key,
    )


def _raise_if_open_quote_exists(response: httpx.Response) -> None:
    """Explain the one-open-quote-per-coldkey rule when the server refuses a quote for it."""
    if response.status_code != 409:
        return
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        return
    if isinstance(detail, dict) and detail.get("code") == "open_quote_exists":
        raise click.ClickException(
            f"Another hotkey of this coldkey holds open quote {detail['quote_id']} for this competition until "
            f"{detail['expires_at']}. If that hotkey already burned for it, finish with `ridges resume-upload "
            f"--quote-id {detail['quote_id']} ...` from that hotkey. Otherwise wait for the quote to expire."
        )


def _check_upload_allowed(
    client: httpx.Client,
    *,
    target: UploadTarget,
    pending: PendingUpload,
    credentials: OpenRouterUploadCredentials,
    set_id: int | None = None,
    use_credit: bool = False,
    credit_id: Optional[str] = None,
) -> dict:
    check_payload = {
        "public_key": pending.public_key,
        "file_info": pending.file_info,
        "signature": pending.signature,
        "name": pending.name,
        "openrouter_api_key": credentials.runtime_api_key,
        "openrouter_management_key": credentials.management_key,
        "pricing_version": PRICING_VERSION,
    }
    if set_id is not None:
        check_payload["set_id"] = set_id
    if use_credit:
        check_payload["use_credit"] = "true"
    if credit_id is not None:
        check_payload["credit_id"] = credit_id
    response = client.post(
        f"{target.api_url}/upload/agent/check",
        files={"agent_file": ("agent.py", target.file_content, "text/plain")},
        data=check_payload,
        timeout=UPLOAD_TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        _raise_if_open_quote_exists(response)
        raise click.ClickException(f"Error checking agent: {response.text}")
    return response.json()


def _unlock_coldkey(wallet) -> None:
    """Unlock the coldkey, re-prompting on incorrect password."""
    from bittensor_wallet.errors import KeyFileError, PasswordError

    while True:
        try:
            wallet.unlock_coldkey()
            return
        except PasswordError:
            console.print("[bold red]Failed:[/bold red] The password used to decrypt your Coldkey keyfile is invalid.")
        except KeyFileError as exc:
            raise click.ClickException(str(exc)) from exc


def _confirm_payment(payment_method_details: dict) -> bool:
    amount_alpha = payment_method_details["amount_alpha_rao"] / 1e9
    payment_netuid = payment_method_details["payment_netuid"]
    price_usd = payment_method_details.get("price_usd")
    balance_alpha = (payment_method_details.get("balance_alpha_rao") or 0) / 1e9
    if price_usd is not None:
        console.print(
            f"\n[cyan]Upload price:[/cyan] ${price_usd:,.2f} (your burn balance: {balance_alpha:,.4f} alpha). "
            "The price rises with each purchased upload in this competition and falls back over time."
        )
    console.print(f"[cyan]Payment Quote ID:[/cyan] {payment_method_details['quote_id']}")
    confirm_payment = Prompt.ask(
        (
            f"\n[bold yellow]Proceed with an IRREVERSIBLE burn of {amount_alpha:,.4f} alpha "
            f"({payment_method_details['amount_alpha_rao']} rao) "
            f"on SN{payment_netuid}?[/bold yellow]"
        ),
        choices=["y", "n"],
        default="n",
    )
    return confirm_payment.lower() == "y"


def _submit_eval_payment(*, wallet, payment_method_details: dict) -> PaymentReceipt:
    from bittensor import Subtensor

    subtensor = Subtensor(network=os.environ.get("SUBTENSOR_NETWORK", "finney"))
    payment_payload = subtensor.substrate.compose_call(
        call_module="SubtensorModule",
        call_function="burn_alpha",
        call_params={
            "hotkey": wallet.hotkey.ss58_address,
            "amount": payment_method_details["amount_alpha_rao"],
            "netuid": payment_method_details["payment_netuid"],
        },
    )

    payment_extrinsic = subtensor.substrate.create_signed_extrinsic(
        call=payment_payload,
        keypair=wallet.coldkey,
    )
    receipt = subtensor.substrate.submit_extrinsic(payment_extrinsic, wait_for_finalization=True)
    if not receipt.is_success:
        error_message = receipt.error_message or "Unknown chain error"
        raise click.ClickException(f"Alpha burn failed on-chain: {error_message}")

    return PaymentReceipt(
        block_hash=receipt.block_hash,
        extrinsic_index=receipt.extrinsic_idx,
        quote_id=payment_method_details["quote_id"],
    )


def _print_payment_receipt(receipt: PaymentReceipt) -> None:
    console.print(
        "\n[yellow]Burn extrinsic submitted. This fee is not refundable and burns are irreversible; "
        "if the upload fails, use this info with `ridges resume-upload` to retry[/yellow]"
    )
    if receipt.quote_id:
        console.print(f"[cyan]Payment Quote ID:[/cyan] {receipt.quote_id}")
    console.print(f"[cyan]Payment Block Hash:[/cyan] {receipt.block_hash}")
    console.print(f"[cyan]Payment Extrinsic Index:[/cyan] {receipt.extrinsic_index}\n")
    if receipt.quote_id:
        console.print(
            "[yellow]To resume: ridges resume-upload "
            f"--quote-id {receipt.quote_id} --payment-block-hash {receipt.block_hash} "
            f"--payment-extrinsic-index {receipt.extrinsic_index}[/yellow]\n"
        )


def _quote_seconds_left(payment_method_details: dict) -> float:
    expires_at = payment_method_details.get("expires_at")
    if not expires_at:
        return float("inf")
    expiry = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
    return (expiry - datetime.now(timezone.utc)).total_seconds()


def _cancel_quote(client: httpx.Client, *, api_url: str, wallet, quote_id: str) -> None:
    """Release a quote that was not burned for. Best effort: an unreleased quote expires on its own."""
    hotkey = wallet.hotkey.ss58_address
    body = {
        "hotkey": hotkey,
        "public_key": wallet.hotkey.public_key.hex(),
        "signature": wallet.hotkey.sign(cancel_signing_string(hotkey, quote_id)).hex(),
    }
    try:
        client.post(f"{api_url}/upload/quote/{quote_id}/cancel", json=body, timeout=UPLOAD_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        console.print(f"[yellow]Could not release quote {quote_id}: {exc}. It expires on its own.[/yellow]")


def _ensure_quote_fresh(client: httpx.Client, *, api_url: str, wallet, payment_method_details: dict) -> None:
    """Never burn against a quote that could expire before the burn lands."""
    if _quote_seconds_left(payment_method_details) < MIN_QUOTE_SECONDS_BEFORE_BURN:
        _cancel_quote(client, api_url=api_url, wallet=wallet, quote_id=payment_method_details["quote_id"])
        raise click.ClickException(
            "The quote expires in under 3 minutes, so it was cancelled and nothing was burned. Run the command again."
        )


def _purchase_quote(client: httpx.Client, *, api_url: str, wallet, quote_id: str) -> Optional[dict]:
    """Buy the upload from the burn balance. Returns None when bought, or the shortfall detail when the balance is short."""
    hotkey = wallet.hotkey.ss58_address
    body = {
        "hotkey": hotkey,
        "public_key": wallet.hotkey.public_key.hex(),
        "signature": wallet.hotkey.sign(purchase_signing_string(hotkey, quote_id)).hex(),
    }
    try:
        response = client.post(f"{api_url}/upload/quote/{quote_id}/purchase", json=body, timeout=UPLOAD_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        raise click.ClickException(f"Could not purchase the upload: {exc}. Your burn is saved as balance.") from exc

    if response.status_code == 200:
        return None

    if response.status_code == 402:
        detail = response.json().get("detail")
        if isinstance(detail, dict) and detail.get("code") == "insufficient_balance":
            return detail
    raise click.ClickException(f"Purchase failed ({response.status_code}): {response.text}")


def _fund_and_purchase(
    client: httpx.Client, *, api_url: str, wallet, details: dict, request_quote, resume_hint: str
) -> Optional[PaymentReceipt]:
    """Burn the quoted gap (if any), confirm it, and buy the upload; repeat with a fresh quote while the price
    moves ahead of the balance. Returns the last receipt, or None when the miner stopped."""
    receipt: Optional[PaymentReceipt] = None
    unlocked = False
    while True:
        quote_id = details["quote_id"]
        if details.get("amount_alpha_rao", 0) > 0:
            if not unlocked:
                _unlock_coldkey(wallet)
                unlocked = True

            if not _confirm_payment(details):
                _cancel_quote(client, api_url=api_url, wallet=wallet, quote_id=quote_id)
                console.print("[bold red]Payment cancelled by user.[/bold red]")
                if receipt is not None:
                    console.print("[yellow]Your earlier burn stays in your balance for any future upload.[/yellow]")
                return None

            _ensure_quote_fresh(client, api_url=api_url, wallet=wallet, payment_method_details=details)
            try:
                receipt = _submit_eval_payment(wallet=wallet, payment_method_details=details)
            except BaseException:
                console.print(
                    "[bold red]The burn submission failed or its confirmation was interrupted. "
                    "It may still have landed on-chain.[/bold red]\n"
                    f"[yellow]Keep this Payment Quote ID:[/yellow] {quote_id}\n"
                    "[yellow]If the burn appears in your wallet/explorer history, resume without burning again:[/yellow]\n"
                    f"  {resume_hint.format(quote_id=quote_id)}"
                )
                raise

            _print_payment_receipt(receipt)
            _confirm_burn(client, api_url=api_url, wallet=wallet, receipt=receipt)
        else:
            console.print(
                f"[cyan]Your burn balance ({(details.get('balance_alpha_rao') or 0) / 1e9:,.4f} alpha) covers the "
                f"${details.get('price_usd', 0):,.2f} upload price; nothing to burn.[/cyan]"
            )
            console.print(f"[cyan]Payment Quote ID:[/cyan] {quote_id} (resume with `--quote-id` alone if interrupted)")
            receipt = PaymentReceipt(block_hash=None, extrinsic_index=None, quote_id=quote_id)

        shortfall = _purchase_quote(client, api_url=api_url, wallet=wallet, quote_id=quote_id)
        if shortfall is None:
            return receipt

        console.print(
            f"[yellow]The price moved to ${shortfall['price_usd']:,.2f} before your purchase landed; "
            f"your balance is {shortfall['balance_alpha_rao'] / 1e9:,.4f} alpha, "
            f"{shortfall['shortfall_alpha_rao'] / 1e9:,.4f} alpha short.[/yellow]"
        )
        if receipt.block_hash is None:
            _cancel_quote(client, api_url=api_url, wallet=wallet, quote_id=quote_id)

        if not Confirm.ask("Burn the difference and continue?", default=True):
            console.print("[yellow]Stopped. Your balance stays on your coldkey for any future upload.[/yellow]")
            return None
        details = request_quote()


def _resolve_resume_receipt(
    quote_id: Optional[str], payment_block_hash: Optional[str], payment_extrinsic_index: Optional[int]
) -> tuple[str, Optional[str], Optional[int]]:
    """Resume inputs: a quote id, plus the receipt when something was burned for it."""
    if quote_id is None:
        quote_id = Prompt.ask("Payment Quote ID")
        if payment_block_hash is None:
            payment_block_hash = (
                Prompt.ask("Payment Block Hash (leave empty if nothing was burned)", default="") or None
            )

    if payment_block_hash is not None and payment_extrinsic_index is None:
        try:
            payment_extrinsic_index = int(Prompt.ask("Payment Extrinsic Index"))
        except ValueError:
            raise click.ClickException("Payment Extrinsic Index must be an integer") from None

    if payment_block_hash is None and payment_extrinsic_index is not None:
        raise click.ClickException("--payment-extrinsic-index needs --payment-block-hash")
    return quote_id, payment_block_hash, payment_extrinsic_index


def _confirm_burn(client: httpx.Client, *, api_url: str, wallet, receipt: PaymentReceipt, attempts: int = 3) -> None:
    """Report a burn right after it lands. Safe to retry: the server confirms each quote once."""
    try:
        block_hash, extrinsic_index = canonical_receipt(receipt.block_hash, receipt.extrinsic_index)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    hotkey = wallet.hotkey.ss58_address
    message = confirm_signing_string(hotkey, receipt.quote_id, block_hash, extrinsic_index)
    body = {
        "quote_id": receipt.quote_id,
        "payment_block_hash": block_hash,
        "payment_extrinsic_index": int(extrinsic_index),
        "hotkey": hotkey,
        "public_key": wallet.hotkey.public_key.hex(),
        "signature": wallet.hotkey.sign(message).hex(),
    }
    for attempt in range(1, attempts + 1):
        try:
            response = client.post(f"{api_url}/upload/payment/confirm", json=body, timeout=UPLOAD_TIMEOUT_SECONDS)
        except httpx.HTTPError as exc:
            if attempt == attempts:
                raise click.ClickException(f"Could not confirm the burn: {exc}") from exc
        else:
            if response.status_code == 200:
                return
            if response.status_code < 500 or attempt == attempts:
                raise click.ClickException(f"Burn confirmation failed ({response.status_code}): {response.text}")
        time.sleep(2 * attempt)


def _print_credit_receipt(receipt: CreditReceipt) -> None:
    console.print(f"\n[cyan]Upload Credit ID:[/cyan] {receipt.credit_id}\n")


def _signed_ticket(
    wallet,
    *,
    funding: str,
    quote_id: Optional[str] = None,
    payment_block_hash: Optional[str] = None,
    payment_extrinsic_index: Optional[int] = None,
    credit_id: Optional[str] = None,
) -> UploadTicket:
    unsigned = UploadTicket(
        hotkey=wallet.hotkey.ss58_address,
        public_key=wallet.hotkey.public_key.hex(),
        funding=funding,
        quote_id=quote_id,
        payment_block_hash=payment_block_hash,
        payment_extrinsic_index=payment_extrinsic_index,
        credit_id=credit_id,
    )
    return sign_ticket(unsigned, wallet.hotkey.sign)


def _print_ticket(ticket: UploadTicket) -> None:
    console.print(
        Panel(
            "[bold cyan]Upload ticket[/bold cyan]\n"
            "Paste this on the Ridges dashboard (Miner -> Upload) to finish the upload there.\n"
            "[bold yellow]Treat it like a password:[/bold yellow] this is a bearer credential — anyone holding it "
            "can upload an agent under your hotkey until it is redeemed.",
            title="Web upload",
            border_style="cyan",
        )
    )
    console.print(encode_ticket(ticket), soft_wrap=True)


def _upload_payload(
    *,
    pending: PendingUpload,
    receipt: PaymentReceipt | CreditReceipt,
    credentials: OpenRouterUploadCredentials,
    set_id: int | None = None,
) -> dict[str, str | int]:
    payload: dict[str, str | int] = {
        "public_key": pending.public_key,
        "file_info": pending.file_info,
        "signature": pending.signature,
        "name": pending.name,
        "openrouter_api_key": credentials.runtime_api_key,
        "openrouter_management_key": credentials.management_key,
    }
    if set_id is not None:
        payload["set_id"] = set_id
    if isinstance(receipt, CreditReceipt):
        payload["credit_id"] = receipt.credit_id
    else:
        if receipt.block_hash is not None and receipt.extrinsic_index is not None:
            payload["payment_block_hash"] = receipt.block_hash
            payload["payment_extrinsic_index"] = receipt.extrinsic_index
        if receipt.quote_id is not None:
            payload["quote_id"] = receipt.quote_id
    return payload


def _submit_upload(client: httpx.Client, *, target: UploadTarget, payload: dict[str, str | int]) -> httpx.Response:
    files = {"agent_file": ("agent.py", target.file_content, "text/plain")}
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task("Signing and uploading...", total=None)
        return client.post(
            f"{target.api_url}/upload/agent",
            files=files,
            data=payload,
            timeout=UPLOAD_TIMEOUT_SECONDS,
        )


def _handle_upload_result(response: httpx.Response, *, name: str) -> None:
    if response.status_code == 200:
        message = response.json().get("message") or f"Miner '{name}' uploaded successfully!"
        console.print(
            Panel(
                f"[bold green]Upload Complete[/bold green]\n[cyan]{message}[/cyan]",
                title="Success",
                border_style="green",
            )
        )
        return

    error = (
        response.json().get("detail", "Unknown error")
        if response.headers.get("content-type", "").startswith("application/json")
        else response.text
    )
    raise click.ClickException(f"Upload failed ({response.status_code}): {error}")


def _prepare_pending_upload(
    *,
    client: httpx.Client,
    wallet: Wallet,
    target: UploadTarget,
    set_id: int,
) -> PendingUpload:
    name, version_num = _resolve_upload_name_and_version(
        client,
        api_url=target.api_url,
        hotkey=wallet.hotkey.ss58_address,
        set_id=set_id,
    )
    return _build_pending_upload(
        wallet=wallet,
        name=name,
        version_num=version_num,
        content_hash=target.content_hash,
    )


def _execute_upload(
    client: httpx.Client,
    *,
    wallet: Wallet,
    target: UploadTarget,
    credentials: OpenRouterUploadCredentials,
    receipt: PaymentReceipt | CreditReceipt,
    set_id: int,
    pending: Optional[PendingUpload] = None,
    run_check: bool = True,
    emit_ticket_on_failure: bool = False,
) -> None:
    """Shared post-payment upload steps used by both upload and resume-upload.

    Parameters
    ----------
    client : httpx.Client
        HTTP Client.
    wallet : Wallet
        Wallet object.
    target : UploadTarget
        Detailed information about the upload endpoint.
    credentials : OpenRouterUploadCredentials
        Open Router credentials
    receipt : PaymentReceipt
        Payment receipt previously submitted.
    run_check : bool, optional
        If True validate if upload is allowed, by default True
    """
    if pending is None:
        pending = _prepare_pending_upload(client=client, wallet=wallet, target=target, set_id=set_id)
    if run_check:
        _check_upload_allowed(client, target=target, pending=pending, credentials=credentials, set_id=set_id)
    payload = _upload_payload(pending=pending, receipt=receipt, credentials=credentials, set_id=set_id)
    try:
        response = _submit_upload(client, target=target, payload=payload)
        _handle_upload_result(response, name=pending.name)
    except Exception:
        # Catches click.ClickException, httpx.HTTPError (timeouts, disconnects, DNS), and
        # malformed response bodies (JSONDecodeError/ValueError/AttributeError from
        # _handle_upload_result) — any exception here means the upload didn't succeed while
        # the payment is already spent, so recovery must print a ticket for all of these too.
        if emit_ticket_on_failure:
            console.print(
                "[yellow]Your payment is safe. Finish the upload on the Ridges dashboard "
                "(Miner → Upload) with this ticket, or run `ridges resume-upload`:[/yellow]"
            )
            if isinstance(receipt, CreditReceipt):
                _print_ticket(_signed_ticket(wallet, funding=FUNDING_CREDIT, credit_id=receipt.credit_id))
            elif receipt.quote_id is not None:
                # Burn tickets require a quote_id; quote-less receipts (owner/synthetic) get no ticket.
                _print_ticket(
                    _signed_ticket(
                        wallet,
                        funding=FUNDING_BURN,
                        quote_id=receipt.quote_id,
                        payment_block_hash=receipt.block_hash,
                        payment_extrinsic_index=None
                        if receipt.extrinsic_index is None
                        else int(receipt.extrinsic_index),
                    )
                )
        raise


def _resolve_wallet(*, coldkey_name: Optional[str], hotkey_name: Optional[str]):
    from bittensor_wallet.wallet import Wallet

    coldkey = coldkey_name or get_or_prompt("RIDGES_COLDKEY_NAME", "Enter your coldkey name", "miner")
    hotkey = hotkey_name or get_or_prompt("RIDGES_HOTKEY_NAME", "Enter your hotkey name", "default")
    return Wallet(name=coldkey, hotkey=hotkey)


def _resolve_target(api_url: str, file: Optional[str]) -> UploadTarget:
    file_path = file or get_or_prompt("RIDGES_AGENT_FILE", "Enter the path to your agent.py file", "agent.py")
    return _read_upload_target(api_url, file_path)


def _resolve_wallet_and_target(
    ctx,
    *,
    file: Optional[str],
    coldkey_name: Optional[str],
    hotkey_name: Optional[str],
):
    """Shared wallet + file resolution used by upload and team-upload."""
    api_url = ctx.obj.get("url") or DEFAULT_API_BASE_URL
    wallet = _resolve_wallet(coldkey_name=coldkey_name, hotkey_name=hotkey_name)
    return wallet, _resolve_target(api_url, file)


@click.command(
    short_help="Upload a miner agent to Ridges.",
    help=format_help(
        "Upload a local agent.py to the Ridges API to enter the competition. "
        "Uploads require both an OpenRouter runtime API key and an OpenRouter management key.",
        "ridges upload --file agent.py",
        "ridges upload --file agent.py --use-credit",
        "ridges upload --file agent.py --coldkey-name miner --hotkey-name default",
    ),
)
@click.option("--file", help="Path to agent.py file")
@click.option("--coldkey-name", help="Coldkey name")
@click.option("--hotkey-name", help="Hotkey name")
@click.option("--competition", type=int, help="Competition set ID to enter.")
@click.option("--use-credit", is_flag=True, help="Use a one-shot upload credit instead of burning alpha.")
@click.option("--credit-id", help="Specific upload credit ID to retry. Requires --use-credit.")
@click.option(
    "--openrouter-api-key",
    help="OpenRouter runtime API key. Falls back to RIDGES_OPENROUTER_API_KEY or an interactive prompt.",
)
@click.option(
    "--openrouter-management-key",
    help="OpenRouter management key. Falls back to RIDGES_OPENROUTER_MANAGEMENT_KEY or an interactive prompt.",
)
@click.pass_context
def upload(
    ctx,
    file: Optional[str],
    coldkey_name: Optional[str],
    hotkey_name: Optional[str],
    competition: Optional[int],
    use_credit: bool,
    credit_id: Optional[str],
    openrouter_api_key: Optional[str],
    openrouter_management_key: Optional[str],
):
    """Upload a miner agent to the Ridges API."""
    if credit_id is not None and not use_credit:
        raise click.ClickException("--credit-id requires --use-credit")

    wallet, target = _resolve_wallet_and_target(ctx, file=file, coldkey_name=coldkey_name, hotkey_name=hotkey_name)
    try:
        with httpx.Client() as client:
            selected_set_id = _select_upload_competition(
                client,
                api_url=target.api_url,
                requested_set_id=competition,
            )
            pending = _prepare_pending_upload(
                client=client,
                wallet=wallet,
                target=target,
                set_id=selected_set_id,
            )
            credentials = _resolve_openrouter_upload_credentials(
                openrouter_api_key=openrouter_api_key,
                openrouter_management_key=openrouter_management_key,
            )
            _print_upload_preview(hotkey=wallet.hotkey.ss58_address, target=target)
            payment_method_details = _check_upload_allowed(
                client,
                target=target,
                pending=pending,
                credentials=credentials,
                set_id=selected_set_id,
                use_credit=use_credit,
                credit_id=credit_id,
            )
            preflight_set_id = payment_method_details.get("set_id")
            if type(preflight_set_id) is not int:
                raise click.ClickException("Server did not return the selected competition; no payment was attempted")

            if preflight_set_id != selected_set_id:
                raise click.ClickException("Server changed the selected competition; no payment was attempted")

            if use_credit:
                if payment_method_details.get("payment_method") != "credit" or not payment_method_details.get(
                    "credit_id"
                ):
                    raise click.ClickException("Server did not provide an upload credit; no burn was attempted")
                receipt = CreditReceipt(credit_id=payment_method_details["credit_id"])
                _print_credit_receipt(receipt)
            else:
                receipt = _fund_and_purchase(
                    client,
                    api_url=target.api_url,
                    wallet=wallet,
                    details=payment_method_details,
                    request_quote=lambda: _check_upload_allowed(
                        client, target=target, pending=pending, credentials=credentials, set_id=selected_set_id
                    ),
                    resume_hint=(
                        "ridges resume-upload --quote-id {quote_id} --payment-block-hash <hash> "
                        "--payment-extrinsic-index <index>"
                    ),
                )
                if receipt is None:
                    return

            _execute_upload(
                client,
                wallet=wallet,
                target=target,
                credentials=credentials,
                receipt=receipt,
                set_id=preflight_set_id,
                pending=pending,
                run_check=False,
                emit_ticket_on_failure=True,
            )

    except click.ClickException:
        raise
    except Exception as exception:
        console.print(f"Error: {exception}", style="bold red")
        raise


@click.command(
    name="team-upload",
    hidden=True,
    short_help="Upload an agent as the platform owner.",
    help=format_help(
        "Upload an agent to Ridges as the platform owner. "
        "The signing hotkey must match the OWNER_HOTKEY configured on the server.",
        "ridges team-upload --file agent.py",
        "ridges team-upload --file agent.py --coldkey-name owner --hotkey-name default",
    ),
)
@click.option("--file", help="Path to agent.py file")
@click.option("--coldkey-name", help="Coldkey name")
@click.option("--hotkey-name", help="Hotkey name")
@click.option("--competition", type=int, help="Competition set ID to enter.")
@click.option(
    "--openrouter-api-key",
    help="OpenRouter runtime API key. Falls back to RIDGES_OPENROUTER_API_KEY or an interactive prompt.",
)
@click.option(
    "--openrouter-management-key",
    help="OpenRouter management key. Falls back to RIDGES_OPENROUTER_MANAGEMENT_KEY or an interactive prompt.",
)
@click.pass_context
def team_upload(
    ctx,
    file: Optional[str],
    coldkey_name: Optional[str],
    hotkey_name: Optional[str],
    competition: Optional[int],
    openrouter_api_key: Optional[str],
    openrouter_management_key: Optional[str],
):
    """Upload an agent as the platform owner."""
    wallet, target = _resolve_wallet_and_target(ctx, file=file, coldkey_name=coldkey_name, hotkey_name=hotkey_name)
    # Create a random Payment Receipt that will be used to generate the Agent ID
    receipt = PaymentReceipt(
        block_hash=_uuid.uuid4().hex,
        extrinsic_index=0,
    )

    try:
        with httpx.Client() as client:
            selected_set_id = _select_upload_competition(
                client,
                api_url=target.api_url,
                requested_set_id=competition,
            )
            credentials = _resolve_openrouter_upload_credentials(
                openrouter_api_key=openrouter_api_key,
                openrouter_management_key=openrouter_management_key,
            )
            _print_upload_preview(hotkey=wallet.hotkey.ss58_address, target=target)
            _execute_upload(
                client,
                wallet=wallet,
                target=target,
                credentials=credentials,
                receipt=receipt,
                set_id=selected_set_id,
                run_check=False,
            )

    except click.ClickException:
        raise
    except Exception as exception:
        console.print(f"Error: {exception}", style="bold red")
        raise


@click.command(
    name="resume-upload",
    short_help="Resume a failed upload using an existing payment receipt.",
    help=format_help(
        "Resume an upload that failed after payment was already submitted on-chain. "
        "Provide the Payment Quote ID, Payment Block Hash, and Payment Extrinsic Index printed after the original payment.",
        "ridges resume-upload --file agent.py --quote-id 2f3b... --payment-block-hash 0x87d2... --payment-extrinsic-index 7",
    ),
)
@click.option("--file", help="Path to agent.py file")
@click.option("--coldkey-name", help="Coldkey name")
@click.option("--hotkey-name", help="Hotkey name")
@click.option("--competition", type=int, help="Competition set ID to enter.")
@click.option(
    "--openrouter-api-key",
    help="OpenRouter runtime API key. Falls back to RIDGES_OPENROUTER_API_KEY or an interactive prompt.",
)
@click.option(
    "--openrouter-management-key",
    help="OpenRouter management key. Falls back to RIDGES_OPENROUTER_MANAGEMENT_KEY or an interactive prompt.",
)
@click.option("--quote-id", help="Payment Quote ID printed after the original payment.")
@click.option("--payment-block-hash", help="Payment Block Hash printed after the original payment.")
@click.option(
    "--payment-extrinsic-index",
    type=int,
    help="Payment Extrinsic Index printed after the original payment.",
)
@click.pass_context
def resume_upload(
    ctx,
    file: Optional[str],
    coldkey_name: Optional[str],
    hotkey_name: Optional[str],
    competition: Optional[int],
    openrouter_api_key: Optional[str],
    openrouter_management_key: Optional[str],
    quote_id: Optional[str],
    payment_block_hash: Optional[str],
    payment_extrinsic_index: Optional[int],
):
    """Resume a failed upload using an existing payment receipt."""
    api_url = ctx.obj.get("url") or DEFAULT_API_BASE_URL
    wallet = _resolve_wallet(coldkey_name=coldkey_name, hotkey_name=hotkey_name)
    try:
        with httpx.Client() as client:
            quote_id, payment_block_hash, payment_extrinsic_index = _resolve_resume_receipt(
                quote_id, payment_block_hash, payment_extrinsic_index
            )
            receipt = PaymentReceipt(
                block_hash=payment_block_hash,
                extrinsic_index=payment_extrinsic_index,
                quote_id=quote_id,
            )
            if receipt.block_hash is not None:
                _confirm_burn(client, api_url=api_url, wallet=wallet, receipt=receipt)
            shortfall = _purchase_quote(client, api_url=api_url, wallet=wallet, quote_id=quote_id)
            if shortfall is not None:
                raise click.ClickException(
                    f"Your balance ({shortfall['balance_alpha_rao'] / 1e9:,.4f} alpha) is "
                    f"{shortfall['shortfall_alpha_rao'] / 1e9:,.4f} alpha short of "
                    f"the ${shortfall['price_usd']:,.2f} upload price. Your burn is saved as balance; run "
                    "`ridges upload` to top up and finish."
                )

            target = _resolve_target(api_url, file)
            selected_set_id = _select_upload_competition(
                client,
                api_url=target.api_url,
                requested_set_id=competition,
            )
            pending = _prepare_pending_upload(
                client=client,
                wallet=wallet,
                target=target,
                set_id=selected_set_id,
            )
            credentials = _resolve_openrouter_upload_credentials(
                openrouter_api_key=openrouter_api_key,
                openrouter_management_key=openrouter_management_key,
            )
            _print_upload_preview(hotkey=wallet.hotkey.ss58_address, target=target)
            _execute_upload(
                client,
                wallet=wallet,
                target=target,
                credentials=credentials,
                receipt=receipt,
                set_id=selected_set_id,
                pending=pending,
                run_check=False,
                emit_ticket_on_failure=True,
            )

    except click.ClickException:
        raise
    except Exception as exception:
        console.print(f"Error: {exception}", style="bold red")
        raise


@click.command(
    short_help="Show your burn balance.",
    help=format_help(
        "Show the alpha you burned that has not yet bought an upload. It is spent automatically on your next "
        "upload in any competition.",
        "ridges balance",
    ),
)
@click.option("--coldkey-name", help="Coldkey name")
@click.option("--hotkey-name", help="Hotkey name")
@click.pass_context
def balance(ctx, coldkey_name: Optional[str], hotkey_name: Optional[str]):
    """Show the coldkey's burn balance."""
    api_url = ctx.obj.get("url") or DEFAULT_API_BASE_URL
    wallet = _resolve_wallet(coldkey_name=coldkey_name, hotkey_name=hotkey_name)
    coldkey = wallet.coldkeypub.ss58_address
    with httpx.Client() as client:
        response = client.get(f"{api_url}/upload/balance", params={"coldkey": coldkey}, timeout=UPLOAD_TIMEOUT_SECONDS)
    if response.status_code != 200:
        raise click.ClickException(f"Could not read the balance: {response.text}")
    console.print(f"[cyan]Burn balance for {coldkey}:[/cyan] {response.json()['balance_alpha_rao'] / 1e9:,.4f} alpha")
