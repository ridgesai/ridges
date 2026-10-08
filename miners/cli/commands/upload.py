"""Upload command for the top-level Ridges CLI."""

from __future__ import annotations

import hashlib
import os
import shlex
import sys
import uuid as _uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

import httpx
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Prompt
from rich.table import Table

from miners.cli.click_ext import click, format_help
from miners.cli.commands.upload_payment import (
    PRICING_HELP,
    PRICING_HELP_CONFIG,
    UPLOAD_TIMEOUT_SECONDS,
    AutoApproval,
    PaymentReceipt,
    PurchaseSummary,
    _confirm_burn,
    _fund_and_purchase,
    _purchase_quote,
    _resolve_resume_receipt,
    _Steps,
    console,
    help_section,
    max_price_option,
    yes_option,
)
from utils.upload_ticket import (
    FUNDING_BURN,
    FUNDING_CREDIT,
    UploadTicket,
    encode_ticket,
    sign_ticket,
)

DEFAULT_API_BASE_URL = "https://agent-upload.ridges.ai"
MAX_AGENT_FILE_SIZE_BYTES = 2 * 1024 * 1024
PRICING_VERSION = 2


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


def _print_upload_preview(*, hotkey: str, target: UploadTarget, set_id: Optional[int] = None) -> None:
    _print_header(
        "Upload" if set_id is None else f"Upload · Competition {set_id}",
        [
            ("Agent", f"{target.agent_path} ({len(target.file_content) / 1024:,.0f} KB)"),
            ("Hotkey", hotkey),
            ("API", target.api_url),
            ("Network", os.environ.get("SUBTENSOR_NETWORK", "finney")),
        ],
    )


def _resume_command_builder(api_url: str, subcommand: str, *extra: str) -> Callable[..., str]:
    """Build the copy-paste command that finishes a funded upload, with the receipt when one exists."""
    base = ["ridges"] if api_url == DEFAULT_API_BASE_URL else ["ridges", "--url", shlex.quote(api_url)]

    def build(quote_id: str, block_hash: Optional[str] = None, extrinsic_index: object = None) -> str:
        parts = [*base, subcommand, "--quote-id", quote_id]
        if block_hash is not None:
            parts += ["--payment-block-hash", str(block_hash), "--payment-extrinsic-index", str(extrinsic_index)]
        return " ".join([*parts, *extra])

    return build


def _wallet_args(wallet) -> list[str]:
    return ["--coldkey-name", shlex.quote(str(wallet.name)), "--hotkey-name", shlex.quote(str(wallet.hotkey_str))]


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


def _print_header(title: str, rows: list[tuple[str, str]]) -> None:
    console.print()
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="cyan")
    grid.add_column()
    for label, value in rows:
        grid.add_row(label, value)
    console.print(Panel(grid, title=title, title_align="left", border_style="cyan"))


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


def _handle_upload_result(response: httpx.Response, *, name: str, purchase: Optional[PurchaseSummary] = None) -> None:
    if response.status_code == 200:
        message = response.json().get("message") or f"Miner '{name}' uploaded successfully!"
        body = f"[bold green]Upload complete[/bold green]\n[cyan]{message}[/cyan]"
        if purchase is not None:
            body += "\n\n" + "\n".join(purchase.lines())
        console.print()
        console.print(Panel(body, title="Success", title_align="left", border_style="green"))
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
    purchase: Optional[PurchaseSummary] = None,
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
        _handle_upload_result(response, name=pending.name, purchase=purchase)
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
        "Upload a local agent.py to enter a competition.\n\n"
        + help_section("Uploads need an OpenRouter runtime API key and an OpenRouter management key.")
        + "\n\n"
        + PRICING_HELP,
        "ridges upload --file agent.py",
        "ridges upload --file agent.py --competition 29 --max-price 50",
        "ridges upload --file agent.py --use-credit",
        "ridges upload --file agent.py --coldkey-name miner --hotkey-name default",
    ),
)
@click.rich_config(help_config=PRICING_HELP_CONFIG)
@click.option("--file", help="Path to agent.py file")
@click.option("--coldkey-name", help="Coldkey name")
@click.option("--hotkey-name", help="Hotkey name")
@click.option("--competition", type=int, help="Competition set ID to enter.")
@max_price_option
@yes_option
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
    max_price: Optional[float],
    assume_yes: bool,
    use_credit: bool,
    credit_id: Optional[str],
    openrouter_api_key: Optional[str],
    openrouter_management_key: Optional[str],
):
    """Upload a miner agent to the Ridges API."""
    if credit_id is not None and not use_credit:
        raise click.ClickException("--credit-id requires --use-credit")
    if use_credit and (max_price is not None or assume_yes):
        raise click.ClickException("--max-price and --yes approve new burns; they don't apply to --use-credit")

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
            _print_upload_preview(hotkey=wallet.hotkey.ss58_address, target=target, set_id=selected_set_id)
            with _Steps() as steps:
                steps.start("Checking your agent and OpenRouter keys")
                payment_method_details = _check_upload_allowed(
                    client,
                    target=target,
                    pending=pending,
                    credentials=credentials,
                    set_id=selected_set_id,
                    use_credit=use_credit,
                    credit_id=credit_id,
                )
                steps.done("Agent and OpenRouter keys checked")
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
                purchase = None
                _print_credit_receipt(receipt)
            else:
                purchase = _fund_and_purchase(
                    client,
                    api_url=target.api_url,
                    wallet=wallet,
                    details=payment_method_details,
                    request_quote=lambda: _check_upload_allowed(
                        client, target=target, pending=pending, credentials=credentials, set_id=selected_set_id
                    ),
                    resume_command=_resume_command_builder(
                        target.api_url,
                        "resume-upload",
                        "--file",
                        shlex.quote(str(target.agent_path)),
                        "--competition",
                        str(selected_set_id),
                        *_wallet_args(wallet),
                    ),
                    set_id=selected_set_id,
                    help_command="ridges upload",
                    approval=AutoApproval(max_price_usd=max_price, assume_yes=assume_yes),
                )
                if purchase is None:
                    return
                receipt = purchase.receipt

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
                purchase=purchase,
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
                    f"Your unused burn ({shortfall['balance_alpha_rao'] / 1e9:,.4f} alpha) is "
                    f"{shortfall['shortfall_alpha_rao'] / 1e9:,.4f} alpha short of "
                    f"the ${shortfall['price_usd']:,.2f} upload price. Nothing is lost: run "
                    "`ridges upload` to burn the difference and finish."
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
    short_help="Show your unused burn.",
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
    console.print(f"[cyan]Unused burn for {coldkey}:[/cyan] {response.json()['balance_alpha_rao'] / 1e9:,.4f} alpha")
