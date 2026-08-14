from __future__ import annotations

import base64
import binascii
import dataclasses
import json
import uuid
from dataclasses import dataclass
from typing import Callable, Optional

from bittensor_wallet.keypair import Keypair

TICKET_VERSION = 2
FUNDING_BURN = "burn"
FUNDING_CREDIT = "credit"

_TICKET_DOMAIN = "ridges-upload-ticket:v2"
_PREPARE_DOMAIN = "ridges-upload-prepare:v2"


@dataclass(frozen=True, slots=True)
class UploadTicket:
    hotkey: str
    public_key: str
    funding: str
    signature: str = ""
    quote_id: Optional[str] = None
    payment_block_hash: Optional[str] = None
    payment_extrinsic_index: Optional[int] = None
    credit_id: Optional[str] = None


def ticket_signing_string(ticket: UploadTicket) -> str:
    if ticket.funding == FUNDING_BURN:
        return (
            f"{_TICKET_DOMAIN}:{ticket.hotkey}:{FUNDING_BURN}:"
            f"{ticket.quote_id}:{ticket.payment_block_hash}:{ticket.payment_extrinsic_index}"
        )

    if ticket.funding == FUNDING_CREDIT:
        return f"{_TICKET_DOMAIN}:{ticket.hotkey}:{FUNDING_CREDIT}:{ticket.credit_id}"

    raise ValueError(f"Unknown ticket funding: {ticket.funding!r}")


def prepare_signing_string(hotkey: str) -> str:
    return f"{_PREPARE_DOMAIN}:{hotkey}"


def sign_ticket(ticket: UploadTicket, signer: Callable[[str], bytes]) -> UploadTicket:
    return dataclasses.replace(ticket, signature=signer(ticket_signing_string(ticket)).hex())


def _validate_fields(ticket: UploadTicket) -> None:
    if (
        not isinstance(ticket.hotkey, str)
        or not ticket.hotkey
        or not isinstance(ticket.public_key, str)
        or not ticket.public_key
        or not isinstance(ticket.signature, str)
        or not ticket.signature
    ):
        raise ValueError("Upload ticket is missing identity fields")

    if not isinstance(ticket.funding, str):
        raise ValueError("Upload ticket funding must be a string")

    if ticket.funding == FUNDING_BURN:
        if (
            not isinstance(ticket.quote_id, str)
            or not ticket.quote_id
            or not isinstance(ticket.payment_block_hash, str)
            or not ticket.payment_block_hash
            or type(ticket.payment_extrinsic_index) is not int
        ):
            raise ValueError("Burn ticket is missing payment fields")

        if ticket.credit_id is not None:
            raise ValueError("Burn ticket must not carry a credit_id")

        uuid.UUID(ticket.quote_id)
        if ticket.payment_extrinsic_index < 0:
            raise ValueError("Burn ticket has an invalid payment_extrinsic_index")

    elif ticket.funding == FUNDING_CREDIT:
        if not isinstance(ticket.credit_id, str) or not ticket.credit_id:
            raise ValueError("Credit ticket is missing credit_id")

        if any(v is not None for v in (ticket.quote_id, ticket.payment_block_hash, ticket.payment_extrinsic_index)):
            raise ValueError("Credit ticket must not carry burn payment fields")

        uuid.UUID(ticket.credit_id)
    else:
        raise ValueError(f"Unknown ticket funding: {ticket.funding!r}")


def encode_ticket(ticket: UploadTicket) -> str:
    _validate_fields(ticket)
    payload = {"v": TICKET_VERSION, **{k: v for k, v in dataclasses.asdict(ticket).items() if v is not None}}
    return base64.b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).decode()


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("Upload ticket has duplicate fields")
    return dict(pairs)


def decode_ticket(blob: str) -> UploadTicket:
    try:
        data = json.loads(
            base64.b64decode(blob.strip().encode(), validate=True).decode(),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exception:
        raise ValueError(f"Not a valid upload ticket: {exception}") from exception

    if not isinstance(data, dict):
        raise ValueError("Unsupported upload ticket version")

    version = data.pop("v", None)
    if type(version) is not int or version != TICKET_VERSION:
        raise ValueError("Unsupported upload ticket version")

    try:
        ticket = UploadTicket(**data)
    except TypeError as exception:
        raise ValueError(f"Upload ticket has missing or unknown fields: {exception}") from exception

    except ValueError as exception:
        raise ValueError(f"Upload ticket is invalid: {exception}") from exception

    try:
        _validate_fields(ticket)
    except ValueError:
        raise

    except Exception as exception:
        raise ValueError(f"Upload ticket is invalid: {exception}") from exception
    return ticket


def verify_ticket_signature(ticket: UploadTicket) -> bool:
    try:
        keypair = Keypair(public_key=ticket.public_key)
        if keypair.ss58_address != ticket.hotkey:
            return False
        return keypair.verify(ticket_signing_string(ticket), bytes.fromhex(ticket.signature))

    except Exception:
        return False
