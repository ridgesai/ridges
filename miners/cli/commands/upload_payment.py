from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

import httpx
from rich.console import Console
from rich.padding import Padding
from rich.progress import Progress, ProgressColumn, SpinnerColumn, TextColumn
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from miners.cli.click_ext import click
from utils.burn_receipt import canonical_receipt
from utils.upload_pricing import ALPHA_BUFFER
from utils.upload_ticket import cancel_signing_string, confirm_signing_string, purchase_signing_string

console = Console()
UPLOAD_TIMEOUT_SECONDS = 120
MIN_QUOTE_SECONDS_BEFORE_BURN = 180
FINALITY_POLL_SECONDS = 2
FINALITY_SLOW_SECONDS = 60
FINALITY_TIMEOUT_SECONDS = 600


def help_section(*lines: str) -> str:
    """One help block rich-click prints as written. Its trailing single-space line is the blank line after it:
    with PRICING_HELP_CONFIG's empty paragraph separator, sections are spaced by exactly one line."""
    return "\b\n" + "\n".join(lines) + "\n "


PRICING_HELP = (
    help_section(
        "[bold]How upload pricing works[/bold]",
        "  • Each competition has one upload price. It rises about 15% with every purchased",
        "    upload and halves every 30 minutes, down to a $5 floor (the defaults).",
        "  • You pay by burning alpha. The upload is bought at the price when your purchase",
        "    lands, not the price you were quoted.",
        "  • Quotes add a 10% buffer for price moves. Alpha you burn but don't need counts",
        "    toward your next upload, in any competition, and never expires (`ridges balance`).",
        "  • If another upload is bought first and your burn falls short, you are asked to",
        "    burn only the difference. Saying no keeps everything you burned.",
    )
    + "\n\n"
    + help_section(
        "[bold]Approving burns automatically[/bold]",
        "  --max-price USD   approve burns and the purchase while the price is at or below USD",
        "  --yes, -y         approve them with no limit",
    )
)


PRICING_HELP_CONFIG = click.RichHelpConfiguration.load_from_globals(
    padding_helptext_first_line=(0, 0, 1, 0), text_paragraph_linebreaks=""
)


max_price_option = click.option(
    "--max-price",
    type=click.FloatRange(min=0, min_open=True),
    metavar="USD",
    help="Approve burns and the purchase automatically while the upload price is at or below this. "
    "Checked before every burn and right before buying.",
)


yes_option = click.option(
    "--yes", "-y", "assume_yes", is_flag=True, help="Approve every burn and the purchase automatically, no limit."
)


@dataclass(frozen=True, slots=True)
class PaymentReceipt:
    """A burn receipt. Both receipt fields are None when the burn balance covered the quote and nothing was burned."""

    block_hash: Optional[str]
    extrinsic_index: Optional[int]
    quote_id: Optional[str] = None


@dataclass(frozen=True, slots=True)
class PriceSnapshot:
    """A competition's live upload price, from the public pricing endpoint."""

    price_usd: float
    alpha_price_usd: float
    floor_usd: float
    half_life_minutes: float
    multiplier: float


@dataclass(frozen=True, slots=True)
class AutoApproval:
    """How burns and the purchase are approved: by asking (the default), automatically up to a price, or always."""

    max_price_usd: Optional[float] = None
    assume_yes: bool = False

    @property
    def auto(self) -> bool:
        return self.assume_yes or self.max_price_usd is not None

    def over_limit(self, price_usd: float) -> bool:
        return self.max_price_usd is not None and price_usd > self.max_price_usd

    def reason(self, price_usd: float) -> str:
        if self.max_price_usd is None:
            return "(--yes)"
        return f"(${price_usd:,.2f} ≤ ${self.max_price_usd:,.2f} limit)"

    def note(self) -> Optional[str]:
        if self.max_price_usd is not None:
            return f"auto-approved under your ${self.max_price_usd:,.2f} limit"
        return "auto-approved (--yes)" if self.assume_yes else None


@dataclass(frozen=True, slots=True)
class PurchaseSummary:
    """What a funded purchase cost, for the closing receipt. `paid_usd` is the live price read just before buying."""

    receipt: PaymentReceipt
    quoted_usd: float
    paid_usd: float
    burned_alpha_rao: int
    burns: int
    balance_alpha_rao: Optional[int]
    approval_note: Optional[str] = None

    def lines(self) -> list[str]:
        price = f"≈ ${self.paid_usd:,.2f} at purchase"
        if abs(self.paid_usd - self.quoted_usd) >= 0.005:
            price += f" (quoted ${self.quoted_usd:,.2f})"
        if self.burns:
            burned = f"{_alpha(self.burned_alpha_rao)} in {self.burns} burn{'s' if self.burns > 1 else ''}"
        else:
            burned = "nothing, paid from unused burn"
        if self.approval_note:
            burned += f", {self.approval_note}"
        if self.balance_alpha_rao is None:
            balance = "see `ridges balance`"
        else:
            balance = f"{_alpha(self.balance_alpha_rao)}, counts toward your next upload"
        return [f"Price     {price}", f"Burned    {burned}", f"Left over {balance}"]


class BurnFailedError(click.ClickException):
    """The burn extrinsic was included but failed on chain, so nothing was burned."""


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


def _alpha(rao: int) -> str:
    return f"{rao / 1e9:,.4f} α"


def _usd_of(rao: int, market: PriceSnapshot) -> str:
    return f"≈ ${rao / 1e9 * market.alpha_price_usd:,.2f}"


def _get_price(client: httpx.Client, *, api_url: str, set_id: int) -> PriceSnapshot:
    """The competition's live price. The alpha rate is backed out of the buffered alpha amount the server quotes."""
    try:
        response = client.get(
            f"{api_url}/upload/eval-pricing", params={"set_id": set_id}, timeout=UPLOAD_TIMEOUT_SECONDS
        )
    except httpx.HTTPError as exc:
        raise click.ClickException(f"Could not read the upload price: {exc}") from exc
    if response.status_code != 200:
        raise click.ClickException(f"Could not read the upload price ({response.status_code}): {response.text}")
    data = response.json()
    price_usd = float(data["price_usd"])
    return PriceSnapshot(
        price_usd=price_usd,
        alpha_price_usd=price_usd * ALPHA_BUFFER * 1e9 / data["amount_alpha_rao"],
        floor_usd=float(data["floor_usd"]),
        half_life_minutes=float(data["half_life_minutes"]),
        multiplier=float(data["multiplier"]),
    )


def _get_balance(client: httpx.Client, *, api_url: str, coldkey: str) -> Optional[int]:
    """The coldkey's burn balance in rao, or None when it cannot be read. Only ever used for display."""
    try:
        response = client.get(f"{api_url}/upload/balance", params={"coldkey": coldkey}, timeout=UPLOAD_TIMEOUT_SECONDS)
        return int(response.json()["balance_alpha_rao"]) if response.status_code == 200 else None
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None


def _minutes_until(price_usd: float, target_usd: float, market: PriceSnapshot) -> Optional[float]:
    """Minutes of decay until the price reaches `target_usd` if nobody buys; None when decay never gets there."""
    if target_usd < market.floor_usd:
        return None
    if price_usd <= target_usd:
        return 0.0
    return market.half_life_minutes * math.log2(price_usd / target_usd)


class _ElapsedColumn(ProgressColumn):
    def render(self, task) -> Text:
        return Text(f"{task.elapsed or 0:.0f} s", style="dim")


class _Steps:
    """A live checklist: a spinner and timer on the running step, a check mark and its time on each finished one.
    Prompts must never run inside it."""

    def __init__(self) -> None:
        self._progress: Optional[Progress] = None
        self._task = None
        self._started = 0.0

    def __enter__(self) -> _Steps:
        return self

    def __exit__(self, *_exc) -> None:
        self._stop()

    def _stop(self) -> None:
        # Stopped while the spinner row still exists, so the transient display erases exactly that row.
        # An emptied display would leave a blank line behind.
        if self._progress is not None:
            self._progress.stop()
            self._progress = None

    def start(self, text: str) -> None:
        self._stop()
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            _ElapsedColumn(),
            console=console,
            transient=True,
        )
        self._task = self._progress.add_task(text, total=None)
        self._progress.start()
        self._started = time.monotonic()

    def update(self, text: str) -> None:
        self._progress.update(self._task, description=text)

    def elapsed(self) -> float:
        return time.monotonic() - self._started

    def done(self, text: Optional[str] = None) -> None:
        elapsed = self.elapsed()
        self._stop()
        if text is not None:
            row = Table.grid(padding=(0, 1))
            row.add_column(width=64)
            row.add_column(width=7, justify="right", style="dim")
            row.add_row(f"  [green]✓[/green] {text}", f"{elapsed:.1f} s")
            console.print(row)


def _say(markup: str, *, mark: str = "", indent: int = 2) -> None:
    """Print prose that wraps under its own indent (and after its mark), never at the terminal edge."""
    if not mark:
        console.print(Padding(Text.from_markup(markup), (0, 0, 0, indent)))
        return
    row = Table.grid(padding=(0, 1))
    row.add_column(no_wrap=True)
    row.add_column()
    row.add_row(Text.from_markup(mark), Text.from_markup(markup))
    console.print(Padding(row, (0, 0, 0, indent)))


def _print_pricing_intro(market: PriceSnapshot, *, help_command: str) -> None:
    console.print()
    for point in (
        "You pay the price when your purchase lands, not the price you are quoted.",
        f"Each purchase raises it {(market.multiplier - 1) * 100:.0f}%; it halves every "
        f"{market.half_life_minutes:g} min, down to ${market.floor_usd:,.2f}.",
        "Alpha you burn but don't need counts toward your next upload, in any competition.",
    ):
        _say(f"[dim]{point}[/dim]", mark="[dim]•[/dim]")
    _say(f"[dim]Full rules: {help_command} --help[/dim]")


def _print_approval_banner(approval: AutoApproval, *, details: dict, market: PriceSnapshot) -> None:
    if approval.max_price_usd is not None:
        balance_rao = details.get("balance_alpha_rao") or 0
        cap_rao = max(0, int(approval.max_price_usd / market.alpha_price_usd * 1e9 * ALPHA_BUFFER) - balance_rao)
        console.print()
        _say(
            f"[dim]Price limit ${approval.max_price_usd:,.2f} (--max-price): burns and the purchase go ahead without "
            f"asking while the price is at or below it. This run burns at most {_alpha(cap_rao)} "
            f"({_usd_of(cap_rao, market)}), buffer included; anything unused counts toward your next upload.[/dim]"
        )
    elif approval.assume_yes:
        console.print()
        _say(
            "[yellow]No price limit (--yes): every burn and the purchase go ahead without asking until the "
            "upload is bought.[/yellow]"
        )


def _print_price_summary(details: dict, market: PriceSnapshot) -> None:
    price_usd = details.get("price_usd", market.price_usd)
    balance_rao = details.get("balance_alpha_rao") or 0
    amount_rao = details.get("amount_alpha_rao", 0)
    decay_pct = (1 - 0.5 ** (1 / market.half_life_minutes)) * 100
    if price_usd <= market.floor_usd + 0.005:
        trend = "at the floor"
    else:
        trend = f"falls ~{decay_pct:.1f}%/min until the next purchase, floor ${market.floor_usd:,.2f}"

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="cyan")
    grid.add_column(justify="right")
    grid.add_column(style="dim")
    grid.add_row("Price now", f"${price_usd:,.2f}", trend)
    grid.add_row("Unused burn", _alpha(balance_rao), "")
    if amount_rao:
        grid.add_row(
            "To burn", _alpha(amount_rao), f"{_usd_of(amount_rao, market)} · price + buffer, minus unused burn"
        )
    else:
        grid.add_row("To burn", "nothing", "your unused burn covers it")
    console.print(Padding(grid, (1, 0, 1, 2)))


def _print_quote_line(details: dict) -> None:
    parts = [f"quote {details['quote_id']}"]
    if details.get("amount_alpha_rao"):
        parts.append(f"{details['amount_alpha_rao']} rao")
    if details.get("expires_at"):
        expiry = datetime.fromisoformat(str(details["expires_at"]).replace("Z", "+00:00")).astimezone(timezone.utc)
        parts.append(f"expires {expiry:%H:%M:%S} UTC")
    console.print(Text("    " + " · ".join(parts), style="dim"))


def _confirm_payment(details: dict, *, first: bool, market: PriceSnapshot) -> bool:
    """Ask before a burn. The amount shown is the fresh quote's, so one answer covers the whole round."""
    amount_rao = details["amount_alpha_rao"]
    what = f"{_alpha(amount_rao)} ({_usd_of(amount_rao, market)})"
    if first:
        question = f"Burn {what} to buy this upload?"
        note = "Burns can't be undone."
    else:
        question = f"Burn {what} more to finish this upload?"
        note = "n wastes nothing."
    prompt = f"\n  [bold bright_blue]? {question}[/bold bright_blue] [dim]{note}[/dim]"
    return Prompt.ask(prompt, choices=["y", "n"], default="y").lower() == "y"


def _confirm_balance_spend(details: dict) -> bool:
    question = f"Use your unused burn to pay ${details.get('price_usd', 0):,.2f} for this upload?"
    return Confirm.ask(f"\n  [bold bright_blue]? {question}[/bold bright_blue]", default=True)


def _print_price_moved(shortfall: dict, *, quote_price_usd: float, market: PriceSnapshot) -> None:
    price_usd = shortfall["price_usd"]
    if price_usd > quote_price_usd + 0.005:
        headline = f"The price rose to ${price_usd:,.2f} before your purchase landed. Another upload was bought first."
    else:
        headline = f"The alpha price fell since your quote, so the ${price_usd:,.2f} price now takes more alpha."
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="cyan")
    grid.add_column(justify="right")
    grid.add_column(style="dim")
    grid.add_row("Unused burn", _alpha(shortfall["balance_alpha_rao"]), "")
    grid.add_row(
        "Short by", _alpha(shortfall["shortfall_alpha_rao"]), _usd_of(shortfall["shortfall_alpha_rao"], market)
    )
    console.print()
    _say(headline, mark="[yellow]![/yellow]")
    console.print(Padding(grid, (0, 0, 1, 4)))


def _print_balance_kept(balance_rao: Optional[int], market: PriceSnapshot) -> None:
    if balance_rao:
        _say(f"{_alpha(balance_rao)} counts toward your next upload, in any competition.", indent=4)


def _print_stopped(*, burned: bool, balance_rao: int, price_usd: float, market: PriceSnapshot) -> None:
    console.print()
    _say("Stopped. Nothing was uploaded." if burned else "Cancelled. Nothing was burned.", mark="[yellow]■[/yellow]")
    if not balance_rao:
        return
    _print_balance_kept(balance_rao, market)
    minutes = _minutes_until(price_usd, balance_rao / 1e9 * market.alpha_price_usd, market)
    if minutes is None:
        _say(f"It is under the ${market.floor_usd:,.2f} floor, so the next upload still needs a small burn.", indent=4)
    elif minutes:
        _say(
            f"At this rate it covers the price in ~{minutes:.0f} min if nobody else buys. Run the same command "
            "then and nothing more is burned.",
            indent=4,
        )


def _print_limit_stop(*, price_usd: float, approval: AutoApproval, balance_rao: Optional[int], market: PriceSnapshot):
    limit = approval.max_price_usd
    console.print()
    _say(
        f"Stopped: the price is ${price_usd:,.2f}, above your ${limit:,.2f} limit. Nothing was uploaded.",
        mark="[yellow]■[/yellow]",
    )
    _print_balance_kept(balance_rao, market)
    minutes = _minutes_until(price_usd, limit, market)
    if minutes:
        _say(f"It falls under ${limit:,.2f} in ~{minutes:.0f} min if nobody else buys. Run it again then.", indent=4)


def _print_resume_hint(command: str) -> None:
    # Never wrapped, so the command copies as one line.
    console.print(Text(f"    {command}", style="bold"), soft_wrap=True)


def _submit_eval_payment(
    *, wallet, payment_method_details: dict, on_included: Optional[Callable[[PaymentReceipt], None]] = None
) -> PaymentReceipt:
    """Burn the quoted alpha, showing each stage: connect, submit, inclusion in a block, then finality.
    `on_included` gets the receipt as soon as the block is known, before finality."""
    from bittensor import Subtensor

    network = os.environ.get("SUBTENSOR_NETWORK", "finney")
    amount_rao = payment_method_details["amount_alpha_rao"]
    console.print(f"\n  Burning {_alpha(amount_rao)} on SN{payment_method_details['payment_netuid']}")
    with _Steps() as steps:
        steps.start(f"Connecting to {network}")
        substrate = Subtensor(network=network).substrate
        steps.done(f"Connected to {network}")

        steps.start("Signing and submitting the burn")
        payment_payload = substrate.compose_call(
            call_module="SubtensorModule",
            call_function="burn_alpha",
            call_params={
                "hotkey": wallet.hotkey.ss58_address,
                "amount": amount_rao,
                "netuid": payment_method_details["payment_netuid"],
            },
        )
        payment_extrinsic = substrate.create_signed_extrinsic(call=payment_payload, keypair=wallet.coldkey)
        steps.update("Submitted, waiting for the next block")
        included = substrate.submit_extrinsic(payment_extrinsic, wait_for_inclusion=True)
        if not included.is_success:
            raise BurnFailedError(f"Alpha burn failed on-chain: {included.error_message or 'Unknown chain error'}")

        block_number = substrate.get_block_number(included.block_hash)
        receipt = PaymentReceipt(
            block_hash=included.block_hash,
            extrinsic_index=included.extrinsic_idx,
            quote_id=payment_method_details["quote_id"],
        )
        steps.done(f"Included in block #{block_number:,}")
        if on_included is not None:
            on_included(receipt)

        steps.start("Finalizing (usually 1-2 more blocks)")
        while substrate.get_block_number(substrate.get_chain_finalised_head()) < block_number:
            if steps.elapsed() > FINALITY_TIMEOUT_SECONDS:
                raise click.ClickException(
                    f"Block #{block_number:,} was not finalized within {FINALITY_TIMEOUT_SECONDS // 60} minutes."
                )
            if steps.elapsed() > FINALITY_SLOW_SECONDS:
                steps.update("Finalizing, slower than usual. Your burn is already in a block, so waiting is safe")
            time.sleep(FINALITY_POLL_SECONDS)
        if str(substrate.get_block_hash(block_number)).lower() != str(included.block_hash).lower():
            raise click.ClickException(
                f"Block #{block_number:,} was replaced before it was finalized. Your burn was most likely included "
                "in a later block: find it in your wallet history and resume with that block hash."
            )
        steps.done("Finalized")
    return receipt


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
        raise click.ClickException(
            f"Could not purchase the upload: {exc}. Your burn counts toward your next upload."
        ) from exc

    if response.status_code == 200:
        return None

    if response.status_code == 402:
        detail = response.json().get("detail")
        if isinstance(detail, dict) and detail.get("code") == "insufficient_balance":
            return detail
    raise click.ClickException(f"Purchase failed ({response.status_code}): {response.text}")


def _burn_with_recovery(*, wallet, details: dict, resume_command: Callable[..., str]) -> PaymentReceipt:
    """Burn, and on any failure or interrupt print the one command that finishes without burning again."""
    included: list[PaymentReceipt] = []
    try:
        return _submit_eval_payment(wallet=wallet, payment_method_details=details, on_included=included.append)
    except BurnFailedError:
        raise
    except BaseException:
        console.print()
        if included:
            burn = included[0]
            _say(
                "[bold]Interrupted after your burn landed in a block.[/bold] Nothing is lost. "
                "Finish without burning again:",
                mark="[bold red]■[/bold red]",
            )
            _print_resume_hint(resume_command(burn.quote_id, burn.block_hash, burn.extrinsic_index))
        else:
            _say(
                "[bold]The burn failed or was interrupted before it reached a block.[/bold] It may still land. "
                f"Keep this quote ID: {details['quote_id']}. If the burn shows up in your wallet history, "
                "finish without burning again:",
                mark="[bold red]■[/bold red]",
            )
            _print_resume_hint(resume_command(details["quote_id"], "<block-hash>", "<extrinsic-index>"))
        raise


def _fund_and_purchase(
    client: httpx.Client,
    *,
    api_url: str,
    wallet,
    details: dict,
    request_quote: Callable[[], dict],
    resume_command: Callable[..., str],
    set_id: int,
    help_command: str,
    approval: AutoApproval = AutoApproval(),
) -> Optional[PurchaseSummary]:
    """Burn the quoted gap (if any), confirm it, and buy the upload; repeat with a fresh quote while the price
    moves ahead of the balance. Returns what the purchase cost, or None when the miner (or the limit) stopped it.

    The price limit is checked against each quote before burning and against the live price right before buying,
    so at most a purchase landing in that split second can move the price past it."""
    market = _get_price(client, api_url=api_url, set_id=set_id)
    _print_pricing_intro(market, help_command=help_command)
    _print_approval_banner(approval, details=details, market=market)
    _print_price_summary(details, market)

    quoted_usd = details.get("price_usd", market.price_usd)
    burned_rao = 0
    burns = 0
    unlocked = False
    while True:
        quote_id = details["quote_id"]
        amount_rao = details.get("amount_alpha_rao", 0)
        quote_price_usd = details.get("price_usd", market.price_usd)
        if approval.over_limit(quote_price_usd):
            _cancel_quote(client, api_url=api_url, wallet=wallet, quote_id=quote_id)
            _print_limit_stop(
                price_usd=quote_price_usd,
                approval=approval,
                balance_rao=details.get("balance_alpha_rao"),
                market=market,
            )
            return None

        _print_quote_line(details)
        if amount_rao > 0:
            if not unlocked:
                _unlock_coldkey(wallet)
                unlocked = True
            if approval.auto:
                console.print(
                    f"\n  [bold]Burn {burns + 1}[/bold] · auto-approved {approval.reason(quote_price_usd)}: "
                    f"{_alpha(amount_rao)} {_usd_of(amount_rao, market)}"
                )
            elif not _confirm_payment(details, first=burns == 0, market=market):
                _cancel_quote(client, api_url=api_url, wallet=wallet, quote_id=quote_id)
                _print_stopped(
                    burned=burns > 0,
                    balance_rao=details.get("balance_alpha_rao") or 0,
                    price_usd=quote_price_usd,
                    market=market,
                )
                return None

            _ensure_quote_fresh(client, api_url=api_url, wallet=wallet, payment_method_details=details)
            receipt = _burn_with_recovery(wallet=wallet, details=details, resume_command=resume_command)
            burned_rao += amount_rao
            burns += 1
            try:
                with _Steps() as steps:
                    steps.start("Ridges is verifying your burn")
                    _confirm_burn(client, api_url=api_url, wallet=wallet, receipt=receipt)
                    steps.done("Ridges verified your burn")
            except click.ClickException:
                console.print()
                _say(
                    "[bold]Your burn landed but could not be verified yet.[/bold] Nothing is lost. Finish with:",
                    mark="[bold red]■[/bold red]",
                )
                _print_resume_hint(resume_command(receipt.quote_id, receipt.block_hash, receipt.extrinsic_index))
                raise
        else:
            if approval.auto:
                console.print(
                    f"\n  Paying ${quote_price_usd:,.2f} from unused burn · "
                    f"auto-approved {approval.reason(quote_price_usd)}"
                )
            elif not _confirm_balance_spend(details):
                _cancel_quote(client, api_url=api_url, wallet=wallet, quote_id=quote_id)
                _print_stopped(burned=burns > 0, balance_rao=0, price_usd=quote_price_usd, market=market)
                return None
            receipt = PaymentReceipt(block_hash=None, extrinsic_index=None, quote_id=quote_id)

        try:
            live = _get_price(client, api_url=api_url, set_id=set_id)
        except click.ClickException:
            if approval.max_price_usd is not None:
                raise click.ClickException(
                    "Could not read the live price to check your --max-price limit, so nothing was bought. "
                    "Any alpha you burned counts toward your next upload; run the command again."
                ) from None
            live = market
        if approval.over_limit(live.price_usd):
            if receipt.block_hash is None:
                _cancel_quote(client, api_url=api_url, wallet=wallet, quote_id=quote_id)
            _print_limit_stop(
                price_usd=live.price_usd,
                approval=approval,
                balance_rao=_get_balance(client, api_url=api_url, coldkey=wallet.coldkeypub.ss58_address),
                market=live,
            )
            return None

        with _Steps() as steps:
            steps.start("Buying the upload")
            shortfall = _purchase_quote(client, api_url=api_url, wallet=wallet, quote_id=quote_id)
            if shortfall is None:
                balance_rao = _get_balance(client, api_url=api_url, coldkey=wallet.coldkeypub.ss58_address)
                left = "" if balance_rao is None else f" · {_alpha(balance_rao)} left over"
                steps.done(f"Purchased at ≈ ${live.price_usd:,.2f}{left}")
        if shortfall is None:
            return PurchaseSummary(
                receipt=receipt,
                quoted_usd=quoted_usd,
                paid_usd=live.price_usd,
                burned_alpha_rao=burned_rao,
                burns=burns,
                balance_alpha_rao=balance_rao,
                approval_note=approval.note(),
            )

        market = live
        _print_price_moved(shortfall, quote_price_usd=quote_price_usd, market=market)
        if receipt.block_hash is None:
            _cancel_quote(client, api_url=api_url, wallet=wallet, quote_id=quote_id)
        with _Steps() as steps:
            steps.start("Getting a fresh quote for the difference")
            details = request_quote()
            steps.done()


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
