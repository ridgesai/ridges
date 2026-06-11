"""Upload command for the top-level Ridges CLI."""

from __future__ import annotations

import hashlib
import os
import uuid as _uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Prompt

from miners.cli.click_ext import click, format_help

console = Console()
DEFAULT_API_BASE_URL = "https://agent-upload.ridges.ai"
UPLOAD_TIMEOUT_SECONDS = 120
MAX_AGENT_FILE_SIZE_BYTES = 2 * 1024 * 1024

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
    block_hash: str
    extrinsic_index: int
    quote_id: Optional[str] = None


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


def _lookup_latest_agent(client: httpx.Client, *, api_url: str, hotkey: str) -> dict | None:
    response = client.get(f"{api_url}/retrieval/agent-by-hotkey?miner_hotkey={hotkey}")
    if response.status_code == 200 and response.json():
        return response.json()
    return None


def _resolve_upload_name_and_version(client: httpx.Client, *, api_url: str, hotkey: str) -> tuple[str, int]:
    latest_agent = _lookup_latest_agent(client, api_url=api_url, hotkey=hotkey)
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


def _check_upload_allowed(
    client: httpx.Client,
    *,
    target: UploadTarget,
    pending: PendingUpload,
    credentials: OpenRouterUploadCredentials,
) -> dict:
    check_payload = {
        "public_key": pending.public_key,
        "file_info": pending.file_info,
        "signature": pending.signature,
        "name": pending.name,
        "openrouter_api_key": credentials.runtime_api_key,
        "openrouter_management_key": credentials.management_key,
    }
    response = client.post(
        f"{target.api_url}/upload/agent/check",
        files={"agent_file": ("agent.py", target.file_content, "text/plain")},
        data=check_payload,
        timeout=UPLOAD_TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
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
    confirm_payment = Prompt.ask(
        (
            f"\n[bold yellow]Proceed with payment of {payment_method_details['amount_rao']} RAO "
            f"({payment_method_details['amount_rao'] / 1e9} TAO) to "
            f"{payment_method_details['send_address']}?[/bold yellow]"
        ),
        choices=["y", "n"],
        default="n",
    )
    return confirm_payment.lower() == "y"


def _submit_eval_payment(*, wallet, payment_method_details: dict) -> PaymentReceipt:
    from bittensor import Subtensor

    subtensor = Subtensor(network=os.environ.get("SUBTENSOR_NETWORK", "finney"))
    payment_payload = subtensor.substrate.compose_call(
        call_module="Balances",
        call_function="transfer_keep_alive",
        call_params={
            "dest": payment_method_details["send_address"],
            "value": payment_method_details["amount_rao"],
        },
    )

    payment_extrinsic = subtensor.substrate.create_signed_extrinsic(
        call=payment_payload,
        keypair=wallet.coldkey,
    )
    receipt = subtensor.substrate.submit_extrinsic(payment_extrinsic, wait_for_finalization=True)
    return PaymentReceipt(
        block_hash=receipt.block_hash,
        extrinsic_index=receipt.extrinsic_idx,
        quote_id=payment_method_details["quote_id"],
    )


def _print_payment_receipt(receipt: PaymentReceipt) -> None:
    console.print(
        "\n[yellow]Payment extrinsic submitted. If something goes wrong with the upload, "
        "you can use this information to get a refund[/yellow]"
    )
    if receipt.quote_id:
        console.print(f"[cyan]Payment Quote ID:[/cyan] {receipt.quote_id}")
    console.print(f"[cyan]Payment Block Hash:[/cyan] {receipt.block_hash}")
    console.print(f"[cyan]Payment Extrinsic Index:[/cyan] {receipt.extrinsic_index}\n")


def _upload_payload(
    *,
    pending: PendingUpload,
    receipt: PaymentReceipt,
    credentials: OpenRouterUploadCredentials,
) -> dict[str, str | int]:
    payload: dict[str, str | int] = {
        "public_key": pending.public_key,
        "file_info": pending.file_info,
        "signature": pending.signature,
        "name": pending.name,
        "payment_block_hash": receipt.block_hash,
        "payment_extrinsic_index": receipt.extrinsic_index,
        "openrouter_api_key": credentials.runtime_api_key,
        "openrouter_management_key": credentials.management_key,
    }
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
        console.print(
            Panel(
                f"[bold green]Upload Complete[/bold green]\n[cyan]Miner '{name}' uploaded successfully![/cyan]",
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


def _prepare_pending_upload(*, client: httpx.Client, wallet: Wallet, target: UploadTarget) -> PendingUpload:
    name, version_num = _resolve_upload_name_and_version(
        client, api_url=target.api_url, hotkey=wallet.hotkey.ss58_address
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
    receipt: PaymentReceipt,
    pending: Optional[PendingUpload] = None,
    run_check: bool = True,
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
        pending = _prepare_pending_upload(client=client, wallet=wallet, target=target)
    if run_check:
        _check_upload_allowed(client, target=target, pending=pending, credentials=credentials)
    payload = _upload_payload(pending=pending, receipt=receipt, credentials=credentials)
    response = _submit_upload(client, target=target, payload=payload)
    _handle_upload_result(response, name=pending.name)


def _resolve_wallet_and_target(
    ctx,
    *,
    file: Optional[str],
    coldkey_name: Optional[str],
    hotkey_name: Optional[str],
):
    """Shared wallet + file resolution used by both upload and resume-upload."""
    from bittensor_wallet.wallet import Wallet

    api_url = ctx.obj.get("url") or DEFAULT_API_BASE_URL
    coldkey = coldkey_name or get_or_prompt("RIDGES_COLDKEY_NAME", "Enter your coldkey name", "miner")
    hotkey = hotkey_name or get_or_prompt("RIDGES_HOTKEY_NAME", "Enter your hotkey name", "default")
    wallet = Wallet(name=coldkey, hotkey=hotkey)
    file_path = file or get_or_prompt("RIDGES_AGENT_FILE", "Enter the path to your agent.py file", "agent.py")
    target = _read_upload_target(api_url, file_path)
    return wallet, target


@click.command(
    short_help="Upload a miner agent to Ridges.",
    help=format_help(
        "Upload a local agent.py to the Ridges API to enter the competition. "
        "Uploads require both an OpenRouter runtime API key and an OpenRouter management key.",
        "ridges upload --file agent.py",
        "ridges upload --file agent.py --coldkey-name miner --hotkey-name default",
    ),
)
@click.option("--file", help="Path to agent.py file")
@click.option("--coldkey-name", help="Coldkey name")
@click.option("--hotkey-name", help="Hotkey name")
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
    openrouter_api_key: Optional[str],
    openrouter_management_key: Optional[str],
):
    """Upload a miner agent to the Ridges API."""
    wallet, target = _resolve_wallet_and_target(ctx, file=file, coldkey_name=coldkey_name, hotkey_name=hotkey_name)
    credentials = _resolve_openrouter_upload_credentials(
        openrouter_api_key=openrouter_api_key,
        openrouter_management_key=openrouter_management_key,
    )
    _print_upload_preview(hotkey=wallet.hotkey.ss58_address, target=target)

    try:
        with httpx.Client() as client:
            pending = _prepare_pending_upload(client=client, wallet=wallet, target=target)
            payment_method_details = _check_upload_allowed(
                client, target=target, pending=pending, credentials=credentials
            )
            _unlock_coldkey(wallet)
            if not _confirm_payment(payment_method_details):
                console.print("[bold red]Payment cancelled by user. Upload aborted.[/bold red]")
                return

            receipt = _submit_eval_payment(wallet=wallet, payment_method_details=payment_method_details)
            _print_payment_receipt(receipt)

            _execute_upload(
                client,
                wallet=wallet,
                target=target,
                credentials=credentials,
                receipt=receipt,
                pending=pending,
                run_check=False,
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
    openrouter_api_key: Optional[str],
    openrouter_management_key: Optional[str],
):
    """Upload an agent as the platform owner."""
    wallet, target = _resolve_wallet_and_target(ctx, file=file, coldkey_name=coldkey_name, hotkey_name=hotkey_name)
    credentials = _resolve_openrouter_upload_credentials(
        openrouter_api_key=openrouter_api_key,
        openrouter_management_key=openrouter_management_key,
    )
    _print_upload_preview(hotkey=wallet.hotkey.ss58_address, target=target)

    # Create a random Payment Receipt that will be used to generate the Agent ID
    receipt = PaymentReceipt(
        block_hash=_uuid.uuid4().hex,
        extrinsic_index=0,
    )

    try:
        with httpx.Client() as client:
            _execute_upload(
                client, wallet=wallet, target=target, credentials=credentials, receipt=receipt, run_check=False
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
@click.option(
    "--openrouter-api-key",
    help="OpenRouter runtime API key. Falls back to RIDGES_OPENROUTER_API_KEY or an interactive prompt.",
)
@click.option(
    "--openrouter-management-key",
    help="OpenRouter management key. Falls back to RIDGES_OPENROUTER_MANAGEMENT_KEY or an interactive prompt.",
)
@click.option("--quote-id", required=True, help="Payment Quote ID printed after the original payment.")
@click.option("--payment-block-hash", required=True, help="Payment Block Hash printed after the original payment.")
@click.option(
    "--payment-extrinsic-index",
    required=True,
    type=int,
    help="Payment Extrinsic Index printed after the original payment.",
)
@click.pass_context
def resume_upload(
    ctx,
    file: Optional[str],
    coldkey_name: Optional[str],
    hotkey_name: Optional[str],
    openrouter_api_key: Optional[str],
    openrouter_management_key: Optional[str],
    quote_id: str,
    payment_block_hash: str,
    payment_extrinsic_index: int,
):
    """Resume a failed upload using an existing payment receipt."""
    wallet, target = _resolve_wallet_and_target(ctx, file=file, coldkey_name=coldkey_name, hotkey_name=hotkey_name)
    credentials = _resolve_openrouter_upload_credentials(
        openrouter_api_key=openrouter_api_key,
        openrouter_management_key=openrouter_management_key,
    )
    _print_upload_preview(hotkey=wallet.hotkey.ss58_address, target=target)

    receipt = PaymentReceipt(
        block_hash=payment_block_hash,
        extrinsic_index=payment_extrinsic_index,
        quote_id=quote_id,
    )

    try:
        with httpx.Client() as client:
            _execute_upload(
                client, wallet=wallet, target=target, credentials=credentials, receipt=receipt, run_check=False
            )

    except click.ClickException:
        raise
    except Exception as exception:
        console.print(f"Error: {exception}", style="bold red")
        raise
