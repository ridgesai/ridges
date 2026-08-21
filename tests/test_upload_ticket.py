import dataclasses
import uuid

import pytest
from bittensor_wallet.keypair import Keypair

from utils.upload_ticket import (
    FUNDING_BURN,
    FUNDING_CREDIT,
    UploadTicket,
    decode_ticket,
    encode_ticket,
    prepare_signing_string,
    sign_ticket,
    ticket_signing_string,
    verify_ticket_signature,
)

KEYPAIR = Keypair.create_from_seed("0x" + "ab" * 32)
OTHER_KEYPAIR = Keypair.create_from_seed("0x" + "cd" * 32)


def _burn_ticket(keypair: Keypair = KEYPAIR) -> UploadTicket:
    unsigned = UploadTicket(
        hotkey=keypair.ss58_address,
        public_key=keypair.public_key.hex(),
        funding=FUNDING_BURN,
        quote_id=str(uuid.uuid4()),
        payment_block_hash="0xdeadbeef1234",
        payment_extrinsic_index=7,
    )
    return sign_ticket(unsigned, keypair.sign)


def _credit_ticket(keypair: Keypair = KEYPAIR) -> UploadTicket:
    unsigned = UploadTicket(
        hotkey=keypair.ss58_address,
        public_key=keypair.public_key.hex(),
        funding=FUNDING_CREDIT,
        credit_id=str(uuid.uuid4()),
    )
    return sign_ticket(unsigned, keypair.sign)


def test_burn_ticket_round_trip_and_verify():
    ticket = _burn_ticket()
    decoded = decode_ticket(encode_ticket(ticket))
    assert decoded == ticket
    assert verify_ticket_signature(decoded)


def test_credit_ticket_round_trip_and_verify():
    ticket = _credit_ticket()
    decoded = decode_ticket(encode_ticket(ticket))
    assert decoded == ticket
    assert verify_ticket_signature(decoded)


def test_signing_strings_are_domain_separated():
    burn = _burn_ticket()
    credit = _credit_ticket()
    assert ticket_signing_string(burn).startswith("ridges-upload-ticket:v2:")
    assert ":burn:" in ticket_signing_string(burn)
    assert ":credit:" in ticket_signing_string(credit)
    assert prepare_signing_string("5FAbc") == "ridges-upload-prepare:v2:5FAbc"


@pytest.mark.parametrize(
    "field, value",
    [
        ("hotkey", OTHER_KEYPAIR.ss58_address),
        ("quote_id", str(uuid.uuid4())),
        ("payment_block_hash", "0xaltered"),
        ("payment_extrinsic_index", 8),
    ],
)
def test_tampered_burn_field_fails_verification(field, value):
    ticket = dataclasses.replace(_burn_ticket(), **{field: value})
    assert not verify_ticket_signature(ticket)


def test_tampered_credit_id_fails_verification():
    ticket = dataclasses.replace(_credit_ticket(), credit_id=str(uuid.uuid4()))
    assert not verify_ticket_signature(ticket)


def test_signature_from_other_keypair_fails():
    forged = dataclasses.replace(_burn_ticket(), signature=_burn_ticket(OTHER_KEYPAIR).signature)
    assert not verify_ticket_signature(forged)


def test_cross_funding_replay_fails():
    burn = _burn_ticket()
    # Re-badge the burn ticket as a credit ticket, keeping the burn signature.
    forged = UploadTicket(
        hotkey=burn.hotkey,
        public_key=burn.public_key,
        funding=FUNDING_CREDIT,
        credit_id=str(uuid.uuid4()),
        signature=burn.signature,
    )
    assert not verify_ticket_signature(forged)


def test_reverse_cross_funding_replay_fails():
    credit = _credit_ticket()
    # Re-badge the credit ticket as a burn ticket, keeping the credit signature.
    forged = UploadTicket(
        hotkey=credit.hotkey,
        public_key=credit.public_key,
        funding=FUNDING_BURN,
        quote_id=str(uuid.uuid4()),
        payment_block_hash="0xdeadbeef1234",
        payment_extrinsic_index=1,
        signature=credit.signature,
    )
    assert not verify_ticket_signature(forged)


def test_public_key_not_matching_hotkey_fails():
    ticket = _burn_ticket()
    mismatched = dataclasses.replace(ticket, public_key=OTHER_KEYPAIR.public_key.hex())
    assert not verify_ticket_signature(mismatched)


@pytest.mark.parametrize(
    "blob",
    [
        "not base64!!!",
        "aGVsbG8=",  # b64 of "hello" — not JSON
        "e30=",  # b64 of "{}" — missing fields
    ],
)
def test_malformed_blobs_raise_value_error(blob):
    with pytest.raises(ValueError):
        decode_ticket(blob)


def test_wrong_version_raises():
    import base64
    import json

    payload = json.loads(base64.b64decode(encode_ticket(_burn_ticket())))
    payload["v"] = 1
    forged = base64.b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).decode()
    with pytest.raises(ValueError):
        decode_ticket(forged)


def test_burn_ticket_missing_burn_fields_raises():
    import base64
    import json

    payload = json.loads(base64.b64decode(encode_ticket(_burn_ticket())))
    payload.pop("quote_id")
    forged = base64.b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).decode()
    with pytest.raises(ValueError):
        decode_ticket(forged)


def test_credit_ticket_with_burn_fields_raises():
    import base64
    import json

    payload = json.loads(base64.b64decode(encode_ticket(_credit_ticket())))
    payload["payment_block_hash"] = "0xdead"
    forged = base64.b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).decode()
    with pytest.raises(ValueError):
        decode_ticket(forged)


def test_non_uuid_ids_raise():
    import base64
    import json

    payload = json.loads(base64.b64decode(encode_ticket(_credit_ticket())))
    payload["credit_id"] = "not-a-uuid"
    forged = base64.b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).decode()
    with pytest.raises(ValueError):
        decode_ticket(forged)


def test_version_as_float_raises():
    import base64
    import json

    payload = json.loads(base64.b64decode(encode_ticket(_burn_ticket())))
    payload["v"] = 2.0
    forged = base64.b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).decode()
    with pytest.raises(ValueError):
        decode_ticket(forged)


def test_burn_extrinsic_index_bool_raises():
    import base64
    import json

    payload = json.loads(base64.b64decode(encode_ticket(_burn_ticket())))
    payload["payment_extrinsic_index"] = True
    forged = base64.b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).decode()
    with pytest.raises(ValueError):
        decode_ticket(forged)


def test_burn_payment_block_hash_int_raises():
    import base64
    import json

    payload = json.loads(base64.b64decode(encode_ticket(_burn_ticket())))
    payload["payment_block_hash"] = 123
    forged = base64.b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).decode()
    with pytest.raises(ValueError):
        decode_ticket(forged)


def test_burn_payment_block_hash_empty_string_raises():
    import base64
    import json

    payload = json.loads(base64.b64decode(encode_ticket(_burn_ticket())))
    payload["payment_block_hash"] = ""
    forged = base64.b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).decode()
    with pytest.raises(ValueError):
        decode_ticket(forged)


def test_hotkey_int_raises():
    import base64
    import json

    payload = json.loads(base64.b64decode(encode_ticket(_burn_ticket())))
    payload["hotkey"] = 5
    forged = base64.b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).decode()
    with pytest.raises(ValueError):
        decode_ticket(forged)


def test_duplicate_json_keys_raise():
    import base64
    import json

    payload = json.loads(base64.b64decode(encode_ticket(_burn_ticket())))
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    # Inject a duplicate "hotkey" entry right after the opening brace.
    duplicated = raw.replace("{", '{"hotkey":"dup",', 1)
    forged = base64.b64encode(duplicated.encode()).decode()
    with pytest.raises(ValueError):
        decode_ticket(forged)
