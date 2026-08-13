from __future__ import annotations

from typing import Optional

import httpx
from bittensor_wallet.wallet import Wallet
from rich.prompt import Prompt

from miners.cli.click_ext import click, format_help
from miners.cli.commands.upload import (
    DEFAULT_API_BASE_URL,
    UPLOAD_TIMEOUT_SECONDS,
    PaymentReceipt,
    _confirm_payment,
    _print_payment_receipt,
    _print_ticket,
    _signed_ticket,
    _submit_eval_payment,
    _unlock_coldkey,
    console,
    get_or_prompt,
)
from utils.upload_ticket import FUNDING_BURN, FUNDING_CREDIT, prepare_signing_string


def _resolve_wallet(coldkey_name: Optional[str], hotkey_name: Optional[str]):
    coldkey = coldkey_name or get_or_prompt("RIDGES_COLDKEY_NAME", "Enter your coldkey name", "miner")
    hotkey = hotkey_name or get_or_prompt("RIDGES_HOTKEY_NAME", "Enter your hotkey name", "default")
    return Wallet(name=coldkey, hotkey=hotkey)


def _post_prepare(api_url: str, *, wallet, use_credit: bool, credit_id: Optional[str]) -> dict:
    body = {
        "hotkey": wallet.hotkey.ss58_address,
        "public_key": wallet.hotkey.public_key.hex(),
        "signature": wallet.hotkey.sign(prepare_signing_string(wallet.hotkey.ss58_address)).hex(),
        "use_credit": use_credit,
    }

    if credit_id is not None:
        body["credit_id"] = credit_id

    with httpx.Client() as client:
        response = client.post(f"{api_url}/upload/prepare", json=body, timeout=UPLOAD_TIMEOUT_SECONDS)

    if response.status_code != 200:
        raise click.ClickException(f"Prepare failed ({response.status_code}): {response.text}")
    return response.json()


@click.command(
    name="prepare-upload",
    short_help="Reserve funding and print a ticket to finish the upload on the web.",
    help=format_help(
        "Reserve an upload (alpha-burn quote by default, or an admin-granted upload credit with "
        "--use-credit), sign a single-use upload ticket with your hotkey, and print it. Paste the "
        "ticket on the Ridges dashboard (Miner -> Upload) together with your agent.py, name, and "
        "OpenRouter keys. The ticket is a bearer credential. Treat it like a password. "
        "Pass an existing receipt (--quote-id/--payment-block-hash/--payment-extrinsic-index) to "
        "mint a ticket for a previous payment without burning again.",
        "ridges prepare-upload",
        "ridges prepare-upload --use-credit",
        "ridges prepare-upload --quote-id 2f3b... --payment-block-hash 0x87d2... --payment-extrinsic-index 7",
    ),
)
@click.option("--coldkey-name", help="Coldkey name")
@click.option("--hotkey-name", help="Hotkey name")
@click.option("--use-credit", is_flag=True, help="Use a one-shot upload credit instead of burning alpha.")
@click.option("--credit-id", help="Specific upload credit ID to retry. Requires --use-credit.")
@click.option("--quote-id", help="Existing Payment Quote ID (resume mode: no new burn).")
@click.option("--payment-block-hash", help="Existing Payment Block Hash (resume mode: no new burn).")
@click.option("--payment-extrinsic-index", type=int, help="Existing Payment Extrinsic Index (resume mode).")
@click.pass_context
def prepare_upload(
    ctx,
    coldkey_name: Optional[str],
    hotkey_name: Optional[str],
    use_credit: bool,
    credit_id: Optional[str],
    quote_id: Optional[str],
    payment_block_hash: Optional[str],
    payment_extrinsic_index: Optional[int],
):
    """Reserve funding + sign, then print a web-upload ticket."""
    if credit_id is not None and not use_credit:
        raise click.ClickException("--credit-id requires --use-credit")

    resume_mode = any(value is not None for value in (quote_id, payment_block_hash, payment_extrinsic_index))
    if resume_mode and use_credit:
        raise click.ClickException("Resume fields describe a burn receipt; do not combine them with --use-credit")

    api_url = ctx.obj.get("url") or DEFAULT_API_BASE_URL
    wallet = _resolve_wallet(coldkey_name, hotkey_name)

    try:
        if resume_mode:
            quote_id = quote_id or Prompt.ask("Payment Quote ID")
            payment_block_hash = payment_block_hash or Prompt.ask("Payment Block Hash")
            if payment_extrinsic_index is None:
                try:
                    payment_extrinsic_index = int(Prompt.ask("Payment Extrinsic Index"))
                except ValueError:
                    raise click.ClickException("Payment Extrinsic Index must be an integer") from None

            ticket = _signed_ticket(
                wallet,
                funding=FUNDING_BURN,
                quote_id=quote_id,
                payment_block_hash=payment_block_hash,
                payment_extrinsic_index=payment_extrinsic_index,
            )

        elif use_credit:
            details = _post_prepare(api_url, wallet=wallet, use_credit=True, credit_id=credit_id)
            if details.get("payment_method") != "credit" or not details.get("credit_id"):
                raise click.ClickException("Server did not reserve an upload credit")
            ticket = _signed_ticket(wallet, funding=FUNDING_CREDIT, credit_id=str(details["credit_id"]))

        else:
            details = _post_prepare(api_url, wallet=wallet, use_credit=False, credit_id=None)
            if details.get("payment_method") != "burn" or not details.get("quote_id"):
                raise click.ClickException("Server did not issue a burn quote")
            _unlock_coldkey(wallet)

            if not _confirm_payment(details):
                console.print("[bold red]Payment cancelled by user. No ticket issued.[/bold red]")
                return

            try:
                receipt: PaymentReceipt = _submit_eval_payment(wallet=wallet, payment_method_details=details)
            except BaseException:
                console.print(
                    "[bold red]The burn submission failed or its confirmation was interrupted. It may still have landed on-chain.[/bold red]\n"
                    f"[yellow]Keep this Payment Quote ID:[/yellow] {details['quote_id']}\n"
                    "[yellow]If the burn appears in your wallet/explorer history, mint your ticket without burning again:[/yellow]\n"
                    f"  ridges prepare-upload --quote-id {details['quote_id']} --payment-block-hash <hash> --payment-extrinsic-index <index>"
                )
                raise

            _print_payment_receipt(receipt)
            ticket = _signed_ticket(
                wallet,
                funding=FUNDING_BURN,
                quote_id=receipt.quote_id,
                payment_block_hash=receipt.block_hash,
                payment_extrinsic_index=int(receipt.extrinsic_index),
            )

        _print_ticket(ticket)

    except click.ClickException:
        raise
    except Exception as exception:
        console.print(f"Error: {exception}", style="bold red")
        raise
