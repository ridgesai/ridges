"""Upload command for the top-level Ridges CLI."""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Prompt

from miners.cli.click_ext import click, format_help

console = Console()
DEFAULT_API_BASE_URL = "https://agent-upload.ridges.ai"
UPLOAD_TIMEOUT_SECONDS = 120


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
    payment_time: float


def get_or_prompt(key: str, prompt: str, default: Optional[str] = None) -> str:
    """Get a value from env or ask interactively."""
    value = os.getenv(key)
    if not value:
        value = Prompt.ask(f"🎯 {prompt}", default=default) if default else Prompt.ask(f"🎯 {prompt}")
    return value


def _resolve_agent_file(path_str: str) -> Path:
    agent_path = Path(path_str).expanduser()
    if not agent_path.exists() or not agent_path.is_file() or agent_path.name != "agent.py":
        raise click.ClickException("File must be named 'agent.py' and exist")
    return agent_path


def _read_upload_target(api_url: str, path_str: str) -> UploadTarget:
    agent_path = _resolve_agent_file(path_str)
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


def _check_upload_allowed(client: httpx.Client, *, target: UploadTarget, pending: PendingUpload) -> None:
    check_payload = {
        "public_key": pending.public_key,
        "file_info": pending.file_info,
        "signature": pending.signature,
        "name": pending.name,
        "payment_time": time.time(),
    }
    response = client.post(
        f"{target.api_url}/upload/agent/check",
        files={"agent_file": ("agent.py", target.file_content, "text/plain")},
        data=check_payload,
        timeout=UPLOAD_TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        raise click.ClickException(f"Error checking agent: {response.text}")


def _fetch_eval_pricing(client: httpx.Client, *, api_url: str) -> dict:
    response = client.get(f"{api_url}/upload/eval-pricing")
    if response.status_code != 200:
        raise click.ClickException("Error fetching evaluation cost")
    return response.json()


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
    payment_time = time.time()
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
        payment_time=payment_time,
    )


def _print_payment_receipt(receipt: PaymentReceipt) -> None:
    console.print(
        "\n[yellow]Payment extrinsic submitted. If something goes wrong with the upload, "
        "you can use this information to get a refund[/yellow]"
    )
    console.print(f"[cyan]Payment Block Hash:[/cyan] {receipt.block_hash}")
    console.print(f"[cyan]Payment Extrinsic Index:[/cyan] {receipt.extrinsic_index}\n")


def _upload_payload(*, pending: PendingUpload, receipt: PaymentReceipt) -> dict[str, str | int | float]:
    return {
        "public_key": pending.public_key,
        "file_info": pending.file_info,
        "signature": pending.signature,
        "name": pending.name,
        "payment_block_hash": receipt.block_hash,
        "payment_extrinsic_index": receipt.extrinsic_index,
        "payment_time": receipt.payment_time,
    }


def _submit_upload(
    client: httpx.Client, *, target: UploadTarget, payload: dict[str, str | int | float]
) -> httpx.Response:
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


@click.command(
    short_help="Upload a miner agent to Ridges.",
    help=format_help(
        "Upload a local agent.py to the Ridges API to enter the competition.",
        "ridges upload --file agent.py",
        "ridges upload --file agent.py --coldkey-name miner --hotkey-name default",
    ),
)
@click.option("--file", help="Path to agent.py file")
@click.option("--coldkey-name", help="Coldkey name")
@click.option("--hotkey-name", help="Hotkey name")
@click.pass_context
def upload(ctx, file: Optional[str], coldkey_name: Optional[str], hotkey_name: Optional[str]):
    """Upload a miner agent to the Ridges API."""
    from bittensor_wallet.wallet import Wallet

    api_url = ctx.obj.get("url") or DEFAULT_API_BASE_URL

    coldkey = coldkey_name or get_or_prompt("RIDGES_COLDKEY_NAME", "Enter your coldkey name", "miner")
    hotkey = hotkey_name or get_or_prompt("RIDGES_HOTKEY_NAME", "Enter your hotkey name", "default")
    wallet = Wallet(name=coldkey, hotkey=hotkey)

    file = file or get_or_prompt("RIDGES_AGENT_FILE", "Enter the path to your agent.py file", "agent.py")
    target = _read_upload_target(api_url, file)
    _print_upload_preview(hotkey=wallet.hotkey.ss58_address, target=target)

    try:
        with httpx.Client() as client:
            name, version_num = _resolve_upload_name_and_version(
                client,
                api_url=target.api_url,
                hotkey=wallet.hotkey.ss58_address,
            )
            pending = _build_pending_upload(
                wallet=wallet,
                name=name,
                version_num=version_num,
                content_hash=target.content_hash,
            )
            _check_upload_allowed(client, target=target, pending=pending)

            payment_method_details = _fetch_eval_pricing(client, api_url=target.api_url)
            if not _confirm_payment(payment_method_details):
                console.print("[bold red]Payment cancelled by user. Upload aborted.[/bold red]")
                return

            receipt = _submit_eval_payment(wallet=wallet, payment_method_details=payment_method_details)
            _print_payment_receipt(receipt)

            payload = _upload_payload(pending=pending, receipt=receipt)
            response = _submit_upload(client, target=target, payload=payload)
            _handle_upload_result(response, name=name)

    except click.ClickException:
        raise
    except Exception as exception:
        console.print(f"Error: {exception}", style="bold red")
        raise
