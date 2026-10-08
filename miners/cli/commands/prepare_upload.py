from __future__ import annotations

import os
from typing import Optional

import httpx
from bittensor_wallet.wallet import Wallet
from rich.panel import Panel

from miners.cli.click_ext import click, format_help
from miners.cli.commands.upload import (
    DEFAULT_API_BASE_URL,
    _print_header,
    _print_ticket,
    _raise_if_open_quote_exists,
    _resume_command_builder,
    _select_upload_competition,
    _signed_ticket,
    _wallet_args,
    get_or_prompt,
)
from miners.cli.commands.upload_payment import (
    PRICING_HELP,
    PRICING_HELP_CONFIG,
    UPLOAD_TIMEOUT_SECONDS,
    AutoApproval,
    PaymentReceipt,
    _confirm_burn,
    _fund_and_purchase,
    _purchase_quote,
    _resolve_resume_receipt,
    console,
    help_section,
    max_price_option,
    yes_option,
)
from utils.upload_ticket import FUNDING_BURN, FUNDING_CREDIT, prepare_signing_string


def _resolve_wallet(coldkey_name: Optional[str], hotkey_name: Optional[str]):
    coldkey = coldkey_name or get_or_prompt("RIDGES_COLDKEY_NAME", "Enter your coldkey name", "miner")
    hotkey = hotkey_name or get_or_prompt("RIDGES_HOTKEY_NAME", "Enter your hotkey name", "default")
    return Wallet(name=coldkey, hotkey=hotkey)


def _post_prepare(
    api_url: str, *, wallet, use_credit: bool, credit_id: Optional[str], set_id: Optional[int] = None
) -> dict:
    body = {
        "hotkey": wallet.hotkey.ss58_address,
        "public_key": wallet.hotkey.public_key.hex(),
        "signature": wallet.hotkey.sign(prepare_signing_string(wallet.hotkey.ss58_address)).hex(),
        "use_credit": use_credit,
    }

    if credit_id is not None:
        body["credit_id"] = credit_id
    if set_id is not None:
        body["set_id"] = set_id

    with httpx.Client() as client:
        response = client.post(f"{api_url}/upload/prepare", json=body, timeout=UPLOAD_TIMEOUT_SECONDS)

    if response.status_code != 200:
        _raise_if_open_quote_exists(response)
        raise click.ClickException(f"Prepare failed ({response.status_code}): {response.text}")
    return response.json()


@click.command(
    name="prepare-upload",
    short_help="Reserve funding and print a ticket to finish the upload on the web.",
    help=format_help(
        "Buy an upload and print a ticket that finishes it on the web.\n\n"
        + help_section(
            "Paste the ticket on the Ridges dashboard (Miner -> Upload) with your agent.py, name and",
            "OpenRouter keys. The upload is bought when the ticket is printed. The ticket is a bearer",
            "credential: treat it like a password.",
        )
        + "\n\n"
        + help_section(
            "--use-credit spends an admin-granted upload credit instead of burning alpha. Pass an existing",
            "receipt (--quote-id, --payment-block-hash, --payment-extrinsic-index) to mint a ticket for an",
            "earlier payment without burning again.",
        )
        + "\n\n"
        + PRICING_HELP,
        "ridges prepare-upload",
        "ridges prepare-upload --competition 29",
        "ridges prepare-upload --competition 29 --max-price 50",
        "ridges prepare-upload --use-credit",
        "ridges prepare-upload --quote-id 2f3b... --payment-block-hash 0x87d2... --payment-extrinsic-index 7",
    ),
)
@click.rich_config(help_config=PRICING_HELP_CONFIG)
@click.option("--coldkey-name", help="Coldkey name")
@click.option("--hotkey-name", help="Hotkey name")
@click.option("--competition", type=int, help="Competition set ID a new burn ticket is for.")
@max_price_option
@yes_option
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
    competition: Optional[int],
    max_price: Optional[float],
    assume_yes: bool,
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
    if (resume_mode or use_credit) and (max_price is not None or assume_yes):
        raise click.ClickException("--max-price and --yes only apply to new burns, not --use-credit or resume mode")

    api_url = ctx.obj.get("url") or DEFAULT_API_BASE_URL
    wallet = _resolve_wallet(coldkey_name, hotkey_name)

    try:
        if resume_mode:
            quote_id, payment_block_hash, payment_extrinsic_index = _resolve_resume_receipt(
                quote_id, payment_block_hash, payment_extrinsic_index
            )
            receipt = PaymentReceipt(
                block_hash=payment_block_hash, extrinsic_index=payment_extrinsic_index, quote_id=quote_id
            )
            with httpx.Client() as client:
                if receipt.block_hash is not None:
                    _confirm_burn(client, api_url=api_url, wallet=wallet, receipt=receipt)
                shortfall = _purchase_quote(client, api_url=api_url, wallet=wallet, quote_id=quote_id)
            if shortfall is not None:
                raise click.ClickException(
                    f"Your unused burn ({shortfall['balance_alpha_rao'] / 1e9:,.4f} alpha) is "
                    f"{shortfall['shortfall_alpha_rao'] / 1e9:,.4f} alpha short of "
                    f"the ${shortfall['price_usd']:,.2f} upload price. Nothing is lost: run "
                    "`ridges prepare-upload` to burn the difference and mint the ticket."
                )
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
            with httpx.Client() as client:
                set_id = _select_upload_competition(client, api_url=api_url, requested_set_id=competition)
                _print_header(
                    f"Upload ticket · Competition {set_id}",
                    [
                        ("Hotkey", wallet.hotkey.ss58_address),
                        ("API", api_url),
                        ("Network", os.environ.get("SUBTENSOR_NETWORK", "finney")),
                    ],
                )

                def request_quote() -> dict:
                    quoted = _post_prepare(api_url, wallet=wallet, use_credit=False, credit_id=None, set_id=set_id)
                    if quoted.get("payment_method") != "burn" or not quoted.get("quote_id"):
                        raise click.ClickException("Server did not issue a burn quote")
                    return quoted

                purchase = _fund_and_purchase(
                    client,
                    api_url=api_url,
                    wallet=wallet,
                    details=request_quote(),
                    request_quote=request_quote,
                    resume_command=_resume_command_builder(api_url, "prepare-upload", *_wallet_args(wallet)),
                    set_id=set_id,
                    help_command="ridges prepare-upload",
                    approval=AutoApproval(max_price_usd=max_price, assume_yes=assume_yes),
                )
            if purchase is None:
                console.print("  No ticket issued.")
                return
            console.print()
            console.print(
                Panel("\n".join(purchase.lines()), title="Upload purchased", title_align="left", border_style="green")
            )
            receipt = purchase.receipt
            ticket = _signed_ticket(
                wallet,
                funding=FUNDING_BURN,
                quote_id=receipt.quote_id,
                payment_block_hash=receipt.block_hash,
                payment_extrinsic_index=None if receipt.extrinsic_index is None else int(receipt.extrinsic_index),
            )

        _print_ticket(ticket)

    except click.ClickException:
        raise
    except Exception as exception:
        console.print(f"Error: {exception}", style="bold red")
        raise
